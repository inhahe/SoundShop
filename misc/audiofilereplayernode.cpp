class AudioFilePlayerNode : public juce::AudioProcessor
{
private:
  juce::AudioBuffer<float> audioBuffer;
  int64_t playbackPosition = 0;
  int64_t startSamplePosition = 0;
  bool isScheduled = false;
  bool isPlaying = false;
  std::string loadedFilename;

public:
  AudioFilePlayerNode()
    : AudioProcessor(BusesProperties()
      .withOutput("Output", juce::AudioChannelSet::stereo(), true))
  {
  }

  // Load an audio file
  bool loadAudioFile(const std::string& filename)
  {
    juce::File audioFile = juce::File::getCurrentWorkingDirectory().getChildFile(filename);

    if (!audioFile.existsAsFile())
    {
      std::cout << "ERROR: Audio file does not exist: " << filename << std::endl;
      return false;
    }

    juce::AudioFormatManager formatManager;
    formatManager.registerBasicFormats();

    std::unique_ptr<juce::AudioFormatReader> reader(formatManager.createReaderFor(audioFile));

    if (reader == nullptr)
    {
      std::cout << "ERROR: Failed to create reader for: " << filename << std::endl;
      return false;
    }

    // Load the audio into our buffer
    audioBuffer.setSize(reader->numChannels, (int)reader->lengthInSamples);
    reader->read(&audioBuffer, 0, (int)reader->lengthInSamples, 0, true, true);

    loadedFilename = filename;
    playbackPosition = 0;
    isPlaying = false;

    std::cout << "Audio file loaded into player node: " << filename
              << ", channels=" << reader->numChannels
              << ", samples=" << reader->lengthInSamples << std::endl;

    return true;
  }

  // Schedule playback to start at a specific sample position
  void schedulePlayback(int64_t startSample, int64_t fileStartPosition = 0)
  {
    startSamplePosition = startSample;
    playbackPosition = juce::jmin(fileStartPosition, (int64_t)audioBuffer.getNumSamples());
    isScheduled = true;
    isPlaying = false;
    std::cout << "Playback scheduled for sample " << startSample
              << ", starting at file position " << playbackPosition << std::endl;
  }

  // Start playing immediately
  void startPlayback(int64_t fileStartPosition = 0)
  {
    playbackPosition = juce::jmin(fileStartPosition, (int64_t)audioBuffer.getNumSamples());
    isPlaying = true;
    isScheduled = false;
    std::cout << "Playback started immediately at file position " << playbackPosition << std::endl;
  }

  // Stop playback
  void stopPlayback()
  {
    isPlaying = false;
    isScheduled = false;
    playbackPosition = 0;
    std::cout << "Playback stopped" << std::endl;
  }

  bool isCurrentlyPlaying() const { return isPlaying; }
  std::string getLoadedFilename() const { return loadedFilename; }

  void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) override
  {
    buffer.clear();

    // Check if scheduled playback should start
    if (isScheduled && !isPlaying && currentSamplePosition >= startSamplePosition)
    {
      isPlaying = true;
      isScheduled = false;
      std::cout << "Started scheduled playback at sample " << currentSamplePosition << std::endl;
    }

    if (!isPlaying || audioBuffer.getNumSamples() == 0)
      return;

    int numSamples = buffer.getNumSamples();
    int samplesToPlay = juce::jmin(numSamples,
      (int)(audioBuffer.getNumSamples() - playbackPosition));

    if (samplesToPlay > 0)
    {
      // Copy audio to output
      for (int ch = 0; ch < buffer.getNumChannels() && ch < audioBuffer.getNumChannels(); ++ch)
      {
        buffer.copyFrom(ch, 0, audioBuffer, ch, (int)playbackPosition, samplesToPlay);
      }

      playbackPosition += samplesToPlay;
    }

    // Check if finished
    if (playbackPosition >= audioBuffer.getNumSamples())
    {
      std::cout << "Playback finished" << std::endl;
      isPlaying = false;
      playbackPosition = 0;
    }
  }

  const juce::String getName() const overri