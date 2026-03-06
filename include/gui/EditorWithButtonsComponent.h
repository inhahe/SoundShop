#pragma once
#include "Common.h"

class PluginHostService;

class EditorWithButtonsComponent : public juce::Component
  {
  public:
    EditorWithButtonsComponent(juce::AudioProcessorEditor* editor,
                                std::unique_ptr<RoutingButton> midiBtn,
                                std::unique_ptr<RoutingButton> virtualBtn)
      : editor(editor),
        midiKeyboardButton(std::move(midiBtn)),
        virtualKeyboardButton(std::move(virtualBtn))
    {
      //cout << "EditorWithButtonsComponent constructor" << endl;

      // Make this component opaque with white background
      setOpaque(true);

      // Paint all children on top of our background
      setBufferedToImage(false);

      // Add the editor - but set it to be non-opaque so our background shows through
      if (editor)
      {
        addAndMakeVisible(editor);
        //cout << "  Editor is opaque: " << editor->isOpaque() << endl;

        // CRITICAL: Force the editor to not be opaque so our background paints behind it
        editor->setOpaque(false);
        //cout << "  Forced editor to be non-opaque. Now editor is opaque: " << editor->isOpaque() << endl;
      }

      // Add the buttons
      if (midiKeyboardButton)
      {
        addAndMakeVisible(midiKeyboardButton.get());
      }
      if (virtualKeyboardButton)
      {
        addAndMakeVisible(virtualKeyboardButton.get());
      }

      //cout << "EditorWithButtonsComponent constructor completed" << endl;
    }

    ~EditorWithButtonsComponent()
    {
      // Editor will be deleted by the window when it takes ownership
      // Just release it from our raw pointer without deleting
    }

    void resized() override
    {
      //cout << "EditorWithButtonsComponent::resized() - bounds: " << getBounds().toString() << endl;

      auto bounds = getLocalBounds();

      // Check if we have buttons
      bool hasButtons = (midiKeyboardButton != nullptr && virtualKeyboardButton != nullptr);
      int buttonHeight = hasButtons ? 50 : 0; // Height of the button strip at the bottom
      int buttonSize = 40;
      int margin = 5;

      // Editor takes the top portion (or all space if no buttons)
      if (editor)
      {
        auto editorBounds = bounds.removeFromTop(bounds.getHeight() - buttonHeight);
        //cout << "  Setting editor bounds: " << editorBounds.toString() << endl;
        editor->setBounds(editorBounds);
      }

      // Position buttons only if they exist
      if (hasButtons)
      {
        // Button strip at the bottom
        auto buttonStrip = bounds; // Remaining area at bottom

        //cout << "  Button strip area: " << buttonStrip.toString() << endl;

        // Center the buttons horizontally in the button strip
        int totalButtonWidth = (buttonSize * 2) + margin;
        int startX = buttonStrip.getX() + (buttonStrip.getWidth() - totalButtonWidth) / 2;
        int buttonY = buttonStrip.getY() + (buttonStrip.getHeight() - buttonSize) / 2;

        //cout << "  Calculated button position: startX=" << startX << ", buttonY=" << buttonY << endl;

        if (midiKeyboardButton)
        {
          auto midiBounds = juce::Rectangle<int>(startX, buttonY, buttonSize, buttonSize);
          //cout << "  Setting midiKeyboardButton bounds: " << midiBounds.toString() << endl;
          midiKeyboardButton->setBounds(midiBounds);
        }

        if (virtualKeyboardButton)
        {
          auto virtualBounds = juce::Rectangle<int>(startX + buttonSize + margin, buttonY, buttonSize, buttonSize);
          //cout << "  Setting virtualKeyboardButton bounds: " << virtualBounds.toString() << endl;
          virtualKeyboardButton->setBounds(virtualBounds);
        }
      }

      //cout << "EditorWithButtonsComponent::resized() completed" << endl;
    }

    void paint(juce::Graphics& g) override
    {
      // Our component is opaque, so we must completely fill the background with a solid colour
      // Paint black background for ALL areas
      g.fillAll(juce::Colours::black);
    }

    std::unique_ptr<RoutingButton>& getMidiButton() { return midiKeyboardButton; }
    std::unique_ptr<RoutingButton>& getVirtualButton() { return virtualKeyboardButton; }

  private:
    juce::AudioProcessorEditor* editor;
    std::unique_ptr<RoutingButton> midiKeyboardButton;
    std::unique_ptr<RoutingButton> virtualKeyboardButton;
  };
