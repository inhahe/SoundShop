#include "services/UiService.h"
#include "host/PluginHostService.h"
#include "gui/VirtualKeyboardWindow.h"

void UiService::start()
{
    // no-op; windows are created lazily
}

void UiService::stop()
{
    virtualKeyboardWindow_.reset();
}

void UiService::showVirtualKeyboard()
{
    if (!virtualKeyboardWindow_)
        virtualKeyboardWindow_ = std::make_unique<VirtualKeyboardWindow>(host_.virtualKeyboardState);

    virtualKeyboardWindow_->setVisible(true);
    virtualKeyboardWindow_->toFront(true);
}

void UiService::hideVirtualKeyboard()
{
    if (virtualKeyboardWindow_)
        virtualKeyboardWindow_->setVisible(false);
}
