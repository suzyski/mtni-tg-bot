#!/usr/bin/env python3
"""
Test topic restriction and decorator behavior for @mtniassistantbot.
"""

from unittest.mock import MagicMock, AsyncMock
import asyncio
import os
import sys

# Set test environment
os.environ["BOT_CHANNEL_ID"] = "-1004342732438"
os.environ["BOT_CHANNEL_TOPIC_ID"] = "621"
os.environ["ADMIN_IDS"] = "1753678802,8995849513,5065850640,6019728260"

from bot import is_admin, is_allowed_topic, require_bot_channel_and_admin, clean_keyboard_command

def make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.effective_message.message_thread_id = thread_id
    update.effective_message.reply_text = AsyncMock()
    return update

async def run_tests():
    print("🧪 Running Topic Restriction Tests...")

    # Test 1: Admin in correct Bot Channel topic (chat: -1004342732438, thread: 621)
    up_good = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    assert is_admin(up_good) == True, "Admin check failed"
    assert is_allowed_topic(up_good) == True, "Allowed topic check failed for thread 621"
    print("  ✅ Test 1: Admin in Bot Channel (topic 621) -> ALLOWED")

    # Test 2: Admin in Order Tracking topic (chat: -1004342732438, thread: 34)
    up_wrong_topic = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=34)
    assert is_allowed_topic(up_wrong_topic) == False, "Topic 34 should NOT be allowed for commands"
    print("  ✅ Test 2: Admin in Order Tracking (topic 34) -> BLOCKED (Correct)")

    # Test 3: Admin in General topic (chat: -1004342732438, thread: None)
    up_general = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=None)
    assert is_allowed_topic(up_general) == False, "General topic (thread None) should NOT be allowed"
    print("  ✅ Test 3: Admin in General Topic (thread None) -> BLOCKED (Correct)")

    # Test 4: Admin in Private DM with bot
    up_dm = make_mock_update(user_id=1753678802, chat_id=1753678802, chat_type="private", thread_id=None)
    assert is_allowed_topic(up_dm) == True, "Private DM should be allowed"
    print("  ✅ Test 4: Admin in 1-on-1 Private DM -> ALLOWED (Correct)")

    # Test 5: Decorator on command in wrong topic removes keyboard
    called = []
    @require_bot_channel_and_admin
    async def sample_command(update, context):
        called.append(True)

    up_blocked = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=34)
    await sample_command(up_blocked, MagicMock())
    assert len(called) == 0, "Handler should not have been called in wrong topic"
    assert up_blocked.effective_message.reply_text.called, "Reply text should have been called"
    
    # Check that reply_markup was ReplyKeyboardRemove
    call_kwargs = up_blocked.effective_message.reply_text.call_args[1]
    from telegram import ReplyKeyboardRemove
    assert isinstance(call_kwargs.get("reply_markup"), ReplyKeyboardRemove), "Must send ReplyKeyboardRemove to clear typing box"
    print("  ✅ Test 5: Decorator intercepts wrong topic & sends ReplyKeyboardRemove -> PASS")

    # Test 6: Non-admin is blocked
    up_nonadmin = make_mock_update(user_id=999999999, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    await sample_command(up_nonadmin, MagicMock())
    assert len(called) == 0, "Non-admin should be blocked"
    print("  ✅ Test 6: Non-admin blocked -> PASS")

    # Test 7: /clean command clears keyboard in any topic
    up_clean = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=34)
    await clean_keyboard_command(up_clean, MagicMock())
    assert up_clean.effective_message.reply_text.called
    clean_kwargs = up_clean.effective_message.reply_text.call_args[1]
    assert isinstance(clean_kwargs.get("reply_markup"), ReplyKeyboardRemove)
    print("  ✅ Test 7: /clean command sends ReplyKeyboardRemove in any topic -> PASS")

    print("\n🎉 ALL 7 TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
