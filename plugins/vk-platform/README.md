# Experimental native Hermes VK platform plugin

This adapter is an experimental native Hermes Gateway platform adapter. It polls
VK via `messages.getConversations(filter=unread)` and routes inbound messages
through the normal Hermes Gateway pipeline.

The standalone bridge in `scripts/hermes_vk_bridge.py` is currently the most
battle-tested deployment mode. Use this plugin if you want to work on native
Hermes platform support.
