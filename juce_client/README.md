This package was split from the original monolithic `juce_client.py`.

Entry point:
  from juce_client import JuceAudioClient

Subpackages:
  juce_client.signals   - signal DAG + caching + wavetable helpers
  juce_client.tempo     - tempo map + beat/sample conversion
  juce_client.daw       - DAW project/graph/render utilities (as originally in the file)

Original file preserved at: juce_client.py
