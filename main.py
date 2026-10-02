                if last_sent_ts and (now_ts - last_sent_ts) < (COOLDOWN_MIN * 60):
                    coins_state[t] = cs
                    continue

                pack = stage_and_signal(t, idx_tr)
                if pack is None:
                    coins_state[t] = cs
                    continue

                stage, direction, strength, vol_mult, h1_chg, d1_chg, reasons, is_agg, is_safe, _, signal_price = pack
                if not is_agg and not is_safe:
                    coins_state[t] = cs
                    continue

                sig_type = "SAFE" if is_safe else "AGG"

                # Одинаковый тип/score на НОВОЙ H1-свече не является дублем.
                signal_cols, signal_rows = get_candles(t, 60, 20)
                begin_index = col_idx(signal_cols, "begin")
                signal_bar = (
                    signal_rows[-1][begin_index]
                    if signal_rows and begin_index is not None else None
                )
                if (signal_bar is not None and cs.get("last_signal_bar") == signal_bar
                    and cs.get("last_type") == sig_type
                    and cs.get("last_stage") == stage
                    and cs.get("last_strength") == strength):
                    coins_state[t] = cs
                    continue

                # confirm (как у тебя)
                confirmed = False
                confirmed_tag = ""
                if sig_type == "SAFE":
                    last_agg_ts = cs.get("last_agg_ts", 0)
                    last_agg_dir = cs.get("last_agg_dir")
                    if last_agg_ts and (now_ts - last_agg_ts) <= (CONFIRM_WINDOW_HOURS * 3600) and last_agg_dir == direction:
                        confirmed = True
                        confirmed_tag = "\n<b>AGGRESSIVE → SAFE подтверждён</b>"

                fire = "🔥" * strength
                emoji = stage_emoji(stage)
                star = " ⭐" if t in PRIORITY_TICKERS else ""

                if sig_type == "AGG":
                    title = "⚠️ <b>AGGRESSIVE</b> — ранний радар"
                    conclusion = "🔴 <b>НЕ ВХОД</b>\n(наблюдать и ждать структуру)"
                else:
                    title = f"✅ <b>SAFE</b>{confirmed_tag}"
                    conclusion = "🟢 <b>МОЖНО ПЛАНИРОВАТЬ</b>\n(вход только по структуре)"

                msg = (
                    f"{title}\n"
                    f"{emoji} <b>{t}{star}</b>\n"
                    f"Стадия: <b>{stage}</b>\n"
                    f"Сила: {fire} ({strength}/5)\n\n"
                    f"H1: {h1_chg:.2f}% | D1: {d1_chg:.2f}%\n"
                    f"Объём: x{vol_mult:.2f}\n\n"
                    "Причины:\n• " + "\n• ".join(reasons) +
                    f"\n\n{memo_intraday()}\n\n"
                    f"🧠 <b>ВЫВОД</b>:\n{conclusion}"
                )

                if not send(msg):
                    coins_state[t] = cs
                    continue

                _journal_record(
                    journal, t, sig_type, direction, 60, 20, score=strength,
                    metadata={"confirmed": confirmed, "stage": stage,
                              "vol_mult": vol_mult, "reasons": reasons},
                )

                # state update (как у тебя + flow отдельно выше)
                cs["last_sent_ts"] = now_ts
                cs["last_type"] = sig_type
                cs["last_stage"] = stage
                cs["last_strength"] = strength
                
                cs["last_signal_price"] = signal_price
                cs["last_signal_direction"] = direction
                cs["last_signal_type"] = sig_type
                cs["last_signal_stage"] = stage
                cs["last_signal_time"] = now_ts
                cs["last_signal_bar"] = signal_bar

                print(
                    f"[SAVE_SIGNAL] {t} "
                    f"{sig_type} "
                    f"{direction} "
                    f"{signal_price}",
                    flush=True
                )
                               

                if sig_type == "AGG":
                    cs["last_agg_ts"] = now_ts
                    cs["last_agg_dir"] = direction
                    stats["agg"] = stats.get("agg", 0) + 1
                    stats["w_agg"] = stats.get("w_agg", 0) + 1
                else:
                    stats["safe"] = stats.get("safe", 0) + 1
                    stats["w_safe"] = stats.get("w_safe", 0) + 1
                    if confirmed:
                        stats["confirmed"] = stats.get("confirmed", 0) + 1
                        stats["w_confirmed"] = stats.get("w_confirmed", 0) + 1

                coins_state[t] = cs

        except Exception as exc:
            print(f"[BOT_ERROR] {type(exc).__name__}: {exc}", flush=True)
            send(f"❌ <b>BOT ERROR</b>: {escape(str(exc))}")
        finally:
            _CANDLES_CACHE = None
            # Успешные отправки сохраняются и при ошибке позже в цикле.
            state["coins"] = coins_state
            state["stats"] = stats
            save_state(state)

        elapsed = time.monotonic() - cycle_started
        delay = max(0.0, CHECK_INTERVAL_SEC - elapsed)
        print(f"[CYCLE_DONE] seconds={elapsed:.1f} next_in={delay:.1f}", flush=True)
        time.sleep(delay)

if __name__ == "__main__":
    run()
