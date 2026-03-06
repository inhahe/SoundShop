#pragma once
#include "Common.h"


class AudioFilePlayerNode : public juce::AudioProcessor
{
public:
    AudioFilePlayerNode()
        : AudioProcessor(BusesProperties().withOutput("Output", juce::AudioChannelSet::stereo(), true))
    {}

    void setTimelineSamplePosition (int64_t pos) noexcept
    {
        currentSamplePosition = pos;
    }

    bool loadAudioFile(const std::string& filename)
    {
        juce::File audioFile = juce::File::getCurrentWorkingDirectory().getChildFile(filename);
        if (!audioFile.existsAsFile())
        {
            //std::cout << "ERROR: Audio file does not exist: " << filename << std::endl;
            return false;
        }

        juce::AudioFormatManager formatManager;
        formatManager.registerBasicFormats();

        std::unique_ptr<juce::AudioFormatReader> reader(formatManager.createReaderFor(audioFile));
        if (reader == nullptr)
        {
            //std::cout << "ERROR: Failed to create reader for: " << filename << std::endl;
            return false;
        }

        audioBuffer.setSize((int)reader->numChannels, (int)reader->lengthInSamples);
        reader->read(&audioBuffer, 0, (int)reader->lengthInSamples, 0, true, true);

        loadedFilename = filename;
        playbackPosition = 0;
        isPlaying = false;
        isScheduled = false;

        //std::cout << "Audio file loaded into player node: " << filename
        //          << ", channels=" << reader->numChannels
        //          << ", samples=" << reader->lengthInSamples << std::endl;

        return true;
    }

    // NEW: schedule region playback (duration-limited + fades + gain)
    void scheduleRegionPlayback(int64_t timelineStartSample,
                               int64_t fileStartPosition,
                               int64_t lengthSamples,
                               float gainLinear = 1.0f,
                               int64_t fadeInSamples = 0,
                               int64_t fadeOutSamples = 0)
    {
        clipStartSamplePosition = timelineStartSample;
        clipEndSamplePosition = timelineStartSample + std::max<int64_t>(0, lengthSamples);

        clipFileStartPosition = juce::jlimit<int64_t>(0, audioBuffer.getNumSamples(), fileStartPosition);
        playbackPosition = clipFileStartPosition;

        clipGainLinear = gainLinear;
        clipFadeInSamples = std::max<int64_t>(0, fadeInSamples);
        clipFadeOutSamples = std::max<int64_t>(0, fadeOutSamples);

        isScheduled = true;
        isPlaying = false;

        //std::cout << "Region scheduled: start=" << clipStartSamplePosition
                  //<< " fileOff=" << clipFileStartPosition
                  //<< " len=" << (clipEndSamplePosition - clipStartSamplePosition)
                  //<< " fadeIn=" << clipFadeInSamples
                  //<< " fadeOut=" << clipFadeOutSamples
                  //<< " gain=" << clipGainLinear << std::endl;
    }

    // Convenience method: start playback immediately from a file position
    void startPlayback(int64_t fileStartPosition)
    {
        int64_t lengthSamples = audioBuffer.getNumSamples() - fileStartPosition;
        if (lengthSamples > 0)
            scheduleRegionPlayback(currentSamplePosition, fileStartPosition, lengthSamples);
    }

    // Convenience method: schedule playback from a file position
    void schedulePlayback(int64_t timelineStartSample, int64_t fileStartPosition)
    {
        int64_t lengthSamples = audioBuffer.getNumSamples() - fileStartPosition;
        if (lengthSamples > 0)
            scheduleRegionPlayback(timelineStartSample, fileStartPosition, lengthSamples);
    }

    void stopPlayback()
    {
        isPlaying = false;
        isScheduled = false;
        playbackPosition = 0;
        //std::cout << "Playback stopped" << std::endl;
    }

    void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer&) override
    {
        buffer.clear();

        // Update global timeline sample position
        // (You likely already have this; shown for clarity)
        // currentSamplePosition should advance by buffer.getNumSamples() externally or here.
        // Assume you maintain it.

        // Start scheduled region when timeline reaches start
        if (isScheduled && !isPlaying && currentSamplePosition >= clipStartSamplePosition)
        {
            isPlaying = true;
            isScheduled = false;
            playbackPosition = clipFileStartPosition;
            //std::cout << "Started scheduled region at sample " << currentSamplePosition << std::endl;
        }

        if (!isPlaying || audioBuffer.getNumSamples() == 0)
            return;

        int numSamples = buffer.getNumSamples();

        // Stop if we’ve reached the clip end in timeline
        int64_t blockStart = currentSamplePosition;
        int64_t blockEnd = currentSamplePosition + numSamples;

        // If block starts after clip end, stop and output silence
        if (blockStart >= clipEndSamplePosition)
        {
            isPlaying = false;
            playbackPosition = 0;
            return;
        }

        // Compute how many samples of this block are within the clip window
        int64_t clipSamplesRemainingInTimeline = clipEndSamplePosition - blockStart;
        int samplesToPlayTimeline = (int)juce::jmin<int64_t>(numSamples, clipSamplesRemainingInTimeline);

        // Also limited by file remaining
        int fileRemaining = audioBuffer.getNumSamples() - (int)playbackPosition;
        int samplesToPlay = juce::jmin(samplesToPlayTimeline, fileRemaining);

        if (samplesToPlay <= 0)
        {
            isPlaying = false;
            playbackPosition = 0;
            return;
        }

        // Copy samples
        for (int ch = 0; ch < buffer.getNumChannels() && ch < audioBuffer.getNumChannels(); ++ch)
            buffer.copyFrom(ch, 0, audioBuffer, ch, (int)playbackPosition, samplesToPlay);

        // Apply gain + fades (linear)
        // Determine clip-local position for each sample: t = (timelineSample - clipStartSamplePosition)
        for (int i = 0; i < samplesToPlay; ++i)
        {
            int64_t timelineSample = blockStart + i;
            int64_t t = timelineSample - clipStartSamplePosition; // 0..clipLen-1
            int64_t clipLen = clipEndSamplePosition - clipStartSamplePosition;

            float env = 1.0f;

            // fade-in
            if (clipFadeInSamples > 0 && t < clipFadeInSamples)
                env *= (float)t / (float)clipFadeInSamples;

            // fade-out
            if (clipFadeOutSamples > 0)
            {
                int64_t tail = clipLen - t; // remaining incl current sample
                if (tail <= clipFadeOutSamples)
                    env *= (float)tail / (float)clipFadeOutSamples;
            }

            float g = clipGainLinear * env;
            for (int ch = 0; ch < buffer.getNumChannels(); ++ch)
                buffer.setSample(ch, i, buffer.getSample(ch, i) * g);
        }

        playbackPosition += samplesToPlay;

        // Stop when we reach clip end (timeline) OR file ends
        if (blockStart + samplesToPlay >= clipEndSamplePosition || playbackPosition >= audioBuffer.getNumSamples())
        {
            isPlaying = false;
            playbackPosition = 0;
        }
    }

    // ... other required AudioProcessor overrides omitted ...

private:
    juce::AudioBuffer<float> audioBuffer;
    std::string loadedFilename;

    int64_t playbackPosition = 0;

    bool isPlaying = false;
    bool isScheduled = false;

    // Timeline tracking (you already have currentSamplePosition somewhere)
public:
    int64_t currentSamplePosition = 0;

private:
    // Region scheduling state
    int64_t clipStartSamplePosition = 0;
    int64_t clipEndSamplePosition = 0;

    int64_t clipFileStartPosition = 0;

    float clipGainLinear = 1.0f;
    int64_t clipFadeInSamples = 0;
    int64_t clipFadeOutSamples = 0;
  const juce::String getName() const override { return "Audio File Player"; }
  void prepareToPlay(double sampleRate, int samplesPerBlock) override {}
  void releaseResources() override {}

  bool acceptsMidi() const override { return false; }
  bool producesMidi() const override { return false; }

  double getTailLengthSeconds() const override { return 0; }

  int getNumPrograms() override { return 1; }
  int getCurrentProgram() override { return 0; }
  void setCurrentProgram(int index) override {}
  const juce::String getProgramName(int index) override { return {}; }
  void changeProgramName(int index, const juce::String& newName) override {}

  void getStateInformation(juce::MemoryBlock& destData) override {}
  void setStateInformation(const void* data, int sizeInBytes) override {}

  juce::AudioProcessorEditor* createEditor() override { return nullptr; }
  bool hasEditor() const override { return false; }
};
