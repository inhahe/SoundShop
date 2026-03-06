#pragma once
#include "Common.h"

class PluginHostService;

class VirtualKeyboardWindow : public juce::DocumentWindow
  {
  public:
    VirtualKeyboardWindow(juce::MidiKeyboardState& state)
      : DocumentWindow("Virtual MIDI Keyboard",
        juce::Desktop::getInstance().getDefaultLookAndFeel()
        .findColour(ResizableWindow::backgroundColourId),
        DocumentWindow::allButtons),
        keyboardState(state)
    {
      setUsingNativeTitleBar(true);

      // Create the keyboard component (2 octaves, horizontal orientation)
      keyboardComponent = std::make_unique<juce::MidiKeyboardComponent>(
        keyboardState,
        juce::MidiKeyboardComponent::horizontalKeyboard);

      // Set keyboard properties
      keyboardComponent->setKeyWidth(40.0f);
      keyboardComponent->setLowestVisibleKey(36);  // C2

      // Set the keyboard as the window content
      setContentNonOwned(keyboardComponent.get(), true);

      // Size the window appropriately (2 octaves = 24 keys)
      int keyboardWidth = 24 * 40 + 50;  // approximate width for 2 octaves
      int keyboardHeight = 120;

      setResizable(true, false);
      setResizeLimits(400, 80, 2000, 200);

      // Position at bottom-center of screen (like a real keyboard)
      juce::Rectangle<int> screenBounds = juce::Desktop::getInstance().getDisplays().getPrimaryDisplay()->userArea;
      int screenWidth = screenBounds.getWidth();
      int screenHeight = screenBounds.getHeight();

      int xPos = (screenWidth - keyboardWidth) / 2;  // Center horizontally
      int yPos = screenHeight - keyboardHeight - 50;  // 50 pixels from bottom

      setBounds(xPos, yPos, keyboardWidth, keyboardHeight);

      //cout << "Virtual keyboard positioned at bottom-center: x=" << xPos << ", y=" << yPos << endl;
    }

    void closeButtonPressed() override
    {
      setVisible(false);
    }

  private:
    juce::MidiKeyboardState& keyboardState;
    std::unique_ptr<juce::MidiKeyboardComponent> keyboardComponent;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(VirtualKeyboardWindow)
  };
