#pragma once
#include "Common.h"

class PluginHostService;

class RoutingButton : public juce::Component
  {
  public:
    enum RoutingType { MidiKeyboard, VirtualKeyboard };

    RoutingButton(RoutingType type, int pluginkey, PluginHostService* hostApp)
      : routingType(type), key(pluginkey), host(hostApp), isActive(false)
    {
      //cout << "RoutingButton constructor: type=" << type << ", key=" << pluginkey << endl;
      setSize(40, 40);
      setMouseCursor(juce::MouseCursor::PointingHandCursor);
    }

    void paint(juce::Graphics& g) override
    {
      //cout << "RoutingButton::paint called for key=" << key << ", type=" << routingType << ", isActive=" << isActive << endl;
      auto bounds = getLocalBounds().reduced(4);

      // Background - more opaque and visible
      if (isActive)
      {
        g.setColour(juce::Colours::lightgreen.withAlpha(0.8f));
        g.fillRoundedRectangle(bounds.toFloat(), 4.0f);
      }
      else
      {
        // Light background for inactive buttons too
        g.setColour(juce::Colour(60, 60, 70)); // Dark blue-grey
        g.fillRoundedRectangle(bounds.toFloat(), 4.0f);
      }

      // Border - brighter colors
      g.setColour(isActive ? juce::Colours::lime : juce::Colours::lightgrey);
      g.drawRoundedRectangle(bounds.toFloat(), 4.0f, 2.0f);

      // Draw miniature keyboard graphic
      if (routingType == MidiKeyboard)
      {
        drawMidiKeyboardIcon(g, bounds.reduced(6));
      }
      else
      {
        drawVirtualKeyboardIcon(g, bounds.reduced(6));
      }
      //cout << "RoutingButton::paint completed for key=" << key << endl;
    }

    void mouseDown(const juce::MouseEvent& event) override
    {
      //cout << "RoutingButton::mouseDown for key=" << key << ", type=" << routingType << endl;
      if (event.mods.isLeftButtonDown())
      {
        toggleRouting();
      }
    }

    void setActive(bool active)
    {
      if (isActive != active)
      {
        isActive = active;
        repaint();
      }
    }

    bool getIsActive() const { return isActive; }
    int getkey() const { return key; }
    RoutingType getRoutingType() const { return routingType; }

  private:
    void drawMidiKeyboardIcon(juce::Graphics& g, juce::Rectangle<int> area)
    {
      // Draw a simplified MIDI keyboard icon with rectangular keys
      g.setColour(isActive ? juce::Colours::white : juce::Colours::lightgrey);

      int numKeys = 7; // 7 white keys
      float keyWidth = area.getWidth() / (float)numKeys;

      // Draw white keys
      for (int i = 0; i < numKeys; ++i)
      {
        float x = (float)area.getX() + i * keyWidth;
        g.drawRect(x, (float)area.getY(), keyWidth, (float)area.getHeight(), 1.5f);
      }

      // Draw black keys (smaller, on top)
      g.setColour(isActive ? juce::Colours::yellow : juce::Colours::silver);
      int blackKeyPattern[] = {1, 1, 0, 1, 1, 1}; // Pattern for black keys
      float blackKeyHeight = area.getHeight() * 0.6f;
      float blackKeyWidth = keyWidth * 0.6f;

      for (int i = 0; i < 6; ++i)
      {
        if (blackKeyPattern[i])
        {
          float x = (float)area.getX() + (i + 0.7f) * keyWidth;
          g.fillRect(x, (float)area.getY(), blackKeyWidth, blackKeyHeight);
        }
      }
    }

    void drawVirtualKeyboardIcon(juce::Graphics& g, juce::Rectangle<int> area)
    {
      // Draw a computer keyboard icon (more rectangular/digital looking)
      g.setColour(isActive ? juce::Colours::white : juce::Colours::lightgrey);

      // Draw outer frame (computer keyboard outline)
      g.drawRoundedRectangle(area.toFloat(), 2.0f, 1.5f);

      // Draw 3 rows of small rectangular keys inside
      auto innerArea = area.reduced(3);
      int rowHeight = innerArea.getHeight() / 3;

      for (int row = 0; row < 3; ++row)
      {
        int numKeysInRow = (row == 1) ? 5 : 4; // Middle row has more keys
        float keyWidth = innerArea.getWidth() / (float)(numKeysInRow + 0.5f);
        float yPos = innerArea.getY() + row * rowHeight + 1;
        float xOffset = (row == 1) ? 0 : keyWidth * 0.25f;

        for (int i = 0; i < numKeysInRow; ++i)
        {
          float xPos = innerArea.getX() + xOffset + i * keyWidth + 1;
          float w = keyWidth - 2;
          float h = rowHeight - 2;
          g.drawRect(xPos, yPos, w, h, 1.0f);
        }
      }
    }

    void toggleRouting();

    RoutingType routingType;
    int key;
    PluginHostService* host;
    bool isActive;
  };
