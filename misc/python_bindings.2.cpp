#include <JuceHeader.h>

int main()
{
    juce::ScopedJuceInitialiser_GUI juceInit;
    
    std::cout << "JUCE Version: " << JUCE_MAJOR_VERSION << "." 
              << JUCE_MINOR_VERSION << "." << JUCE_BUILDNUMBER << std::endl;
    
    return 0;
}