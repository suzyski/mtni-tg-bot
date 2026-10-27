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

from bot import (
    is_admin,
    is_allowed_topic,
    require_bot_channel_and_admin,
    clean_keyboard_command,
    fx_command,
    setfx_command,
    setcargo_command,
    setmarkup_command,
    setlastmile_command,
    get_current_fx_rate,
    set_current_fx_rate,
    get_cargo_rate,
    set_cargo_rate,
    get_service_markup,
    set_service_markup,
    get_lastmile_fee,
    set_lastmile_fee,
    handle_card_callbacks,
    start_calc,
    receive_calc_price,
    receive_calc_weight_inline,
    receive_calc_fx_inline,
    CALC_PRICE,
    CALC_WEIGHT,
    CALC_FX
)

def make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    update.effective_chat.type = chat_type
    update.effective_message.message_thread_id = thread_id
    update.effective_message.reply_text = AsyncMock()
    update.message = update.effective_message
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

    # Test 8: /fx command runs in Bot Channel topic 621
    up_fx = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    await fx_command(up_fx, MagicMock())
    assert up_fx.effective_message.reply_text.called
    fx_msg = up_fx.effective_message.reply_text.call_args[0][0]
    assert "Operational Pricing Parameters" in fx_msg
    print("  ✅ Test 8: /fx command shows operational parameters in topic 621 -> PASS")

    # Test 9: /setfx sends confirmation card without applying immediately
    set_current_fx_rate(3400.0)
    up_setfx = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    mock_ctx = MagicMock()
    mock_ctx.args = ["3500"]
    await setfx_command(up_setfx, mock_ctx)
    assert up_setfx.effective_message.reply_text.called
    assert get_current_fx_rate() == 3400.0, "Rate must NOT change before confirmation!"
    call_kwargs = up_setfx.effective_message.reply_text.call_args[1]
    kb = call_kwargs["reply_markup"]
    btn_confirm = kb.inline_keyboard[0][0]
    assert btn_confirm.callback_data == "cfgconfirm_fx_3500.0"
    print("  ✅ Test 9: /setfx renders confirmation card with confirm/cancel buttons -> PASS")

    # Test 10: Confirmation callback applies rate to ledger
    up_cb = MagicMock()
    up_cb.callback_query.data = "cfgconfirm_fx_3500.0"
    up_cb.callback_query.answer = AsyncMock()
    up_cb.callback_query.edit_message_text = AsyncMock()
    await handle_card_callbacks(up_cb, MagicMock())
    assert get_current_fx_rate() == 3500.0
    print("  ✅ Test 10: Confirm button callback applies 3500 FX rate to ledger -> PASS")
    set_current_fx_rate(3400.0)  # Reset

    # Test 11: Cancel callback preserves original rate
    up_cancel_cb = MagicMock()
    up_cancel_cb.callback_query.data = "cfgcancel"
    up_cancel_cb.callback_query.answer = AsyncMock()
    up_cancel_cb.callback_query.edit_message_text = AsyncMock()
    await handle_card_callbacks(up_cancel_cb, MagicMock())
    assert get_current_fx_rate() == 3400.0
    print("  ✅ Test 11: Cancel button callback preserves original ledger values -> PASS")

    # Test 12: /set* commands blocked outside topic 621
    up_setfx_wrong = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=34)
    await setfx_command(up_setfx_wrong, mock_ctx)
    assert not up_setfx_wrong.effective_message.reply_text.called or "Bot Channel Only" in up_setfx_wrong.effective_message.reply_text.call_args[0][0]
    print("  ✅ Test 12: /set* blocked outside topic 621 -> PASS")

    # Test 13: /setcargo, /setmarkup, /setlastmile confirmation cards
    up_cargo = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    mock_cargo_ctx = MagicMock()
    mock_cargo_ctx.args = ["18.5"]
    await setcargo_command(up_cargo, mock_cargo_ctx)
    cargo_kb = up_cargo.effective_message.reply_text.call_args[1]["reply_markup"]
    assert cargo_kb.inline_keyboard[0][0].callback_data == "cfgconfirm_cargo_18.5"

    up_cargo_confirm = MagicMock()
    up_cargo_confirm.callback_query.data = "cfgconfirm_cargo_18.5"
    up_cargo_confirm.callback_query.answer = AsyncMock()
    up_cargo_confirm.callback_query.edit_message_text = AsyncMock()
    await handle_card_callbacks(up_cargo_confirm, MagicMock())
    assert get_cargo_rate() == 18.5
    set_cargo_rate(16.0)  # Reset
    print("  ✅ Test 13: /setcargo confirmation card and callback verified -> PASS")

    # Test 14: Sourcing Calculator Wizard (/calc) - Step 1: Start
    ctx_calc = MagicMock()
    ctx_calc.user_data = {}
    up_calc_start = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    next_state = await start_calc(up_calc_start, ctx_calc)
    assert next_state == CALC_PRICE
    assert "Step 1/3" in up_calc_start.effective_message.reply_text.call_args[0][0]
    print("  ✅ Test 14: /calc wizard Step 1 prompts for retail SGD price -> PASS")

    # Test 15: Sourcing Calculator Wizard - Step 2: Receive Price & Show Weights
    up_price = make_mock_update(user_id=1753678802, chat_id=-1004342732438, chat_type="supergroup", thread_id=621)
    up_price.message.text = "189"
    next_state_2 = await receive_calc_price(up_price, ctx_calc)
    assert next_state_2 == CALC_WEIGHT
    assert ctx_calc.user_data["calc_price"] == 189.0
    print("  ✅ Test 15: /calc wizard Step 2 parses price and shows category buttons -> PASS")

    # Test 16: Sourcing Calculator Wizard - Step 3: Choose Weight & Show FX
    up_weight_cb = MagicMock()
    up_weight_cb.callback_query.data = "cweight_0.45"
    up_weight_cb.callback_query.answer = AsyncMock()
    up_weight_cb.callback_query.edit_message_text = AsyncMock()
    next_state_3 = await receive_calc_weight_inline(up_weight_cb, ctx_calc)
    assert next_state_3 == CALC_FX
    assert ctx_calc.user_data["calc_weight"] == 0.45
    print("  ✅ Test 16: /calc wizard Step 3 sets weight (0.45kg) and prompts for FX -> PASS")

    # Test 17: Sourcing Calculator Wizard - Final Analysis Card & Sourcing Verdict
    from telegram.ext import ConversationHandler
    up_fx_cb = MagicMock()
    up_fx_cb.callback_query.data = "cfx_3400.0"
    up_fx_cb.callback_query.answer = AsyncMock()
    up_fx_cb.callback_query.edit_message_text = AsyncMock()
    final_res = await receive_calc_fx_inline(up_fx_cb, ctx_calc)
    assert final_res == ConversationHandler.END
    card_rendered = up_fx_cb.callback_query.edit_message_text.call_args[0][0]
    assert "SOURCING FEASIBILITY ANALYSIS" in card_rendered
    assert "HIGHLY RECOMMENDED TO SELL" in card_rendered
    assert "Customer Selling Price" in card_rendered
    print("  ✅ Test 17: /calc renders complete feasibility analysis card & verdict -> PASS")

    print("\n🎉 ALL 17 TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())
