"""Знакомство при первом запуске: персонаж (по умолчанию Лира) сам представляется голосом, проверяет микрофон,
показывает кнопки виджета и предлагает фразы попробовать. Без модели — текст готовый, говорит голос персонажа.

Запуск: сам — при первом запуске (настройка ui.intro == "", у прежних пользователей при обновлении ставится "done"),
когда виджет на связи и слух поднялся; заново — кнопкой «Познакомиться» (команда intro_start). Шаги идут событием
«intro» {step, …}: виджет подсвечивает кнопки и показывает фразы-кнопки, студия — ход знакомства.
Проверка микрофона: на время ожидания следующая распознанная фраза (с именем или без) не идёт модели, а достаётся
знакомству (handle_text → _intro_take); громкость с микрофона копится в пик (_level)."""
import threading
import time

from engine_base import Turn, log


class IntroMixin:
    _intro_ear = None             # {"ev", "text", "peak"} — пока знакомство ждёт фразу
    _intro_probe = None           # {"n", "peak"} — уровень микрофона, пока знакомство говорит (есть ли микрофон вообще)
    _intro_running = False
    _intro_wait_s = 12.0          # сколько ждать фразу при проверке микрофона, с

    # ---------- запуск ----------

    def intro_auto(self):
        """Фоновый поток при старте: первый запуск — дождаться виджета и слуха (до 3 минут) и познакомиться."""
        if self.wav_input or getattr(self, "server", False) or (self.s.get("ui", "intro") or "") != "":
            return
        end = time.monotonic() + 180
        while time.monotonic() < end and not self.stopping.is_set():
            ui_on = bool(getattr(getattr(self, "bridge", None), "clients", None))
            if ui_on and getattr(self, "_audio_thread", None) is not None:
                break
            time.sleep(1.0)
        if self.stopping.is_set() or (self.s.get("ui", "intro") or "") != "":
            return
        self.stopping.wait(2.5)                       # окна успели открыться и отрисоваться
        self.intro_start()

    def intro_start(self):
        """Начать знакомство (повторный вызов во время знакомства ничего не делает). -> True, если началось."""
        if self._intro_running:
            return False
        self._intro_running = True
        threading.Thread(target=self._intro_run, name="intro", daemon=True).start()
        return True

    def intro_stop(self):
        """«Пропустить»: замолчать, знакомство закончено."""
        if not self._intro_running:
            return False
        self._intro_stop = True
        ear = self._intro_ear
        if ear:
            ear["ev"].set()
        self.cancel_turn()
        return True

    # ---------- перехват фразы и громкости ----------

    def _intro_take(self, text, forced=None):
        """Из handle_text: знакомство ждёт фразу — забираем её себе. -> True, если забрали."""
        ear = self._intro_ear
        if ear is None or ear["ev"].is_set():
            return False
        if forced and not (text or "").strip():
            text = self.personas.get(forced)["name"]  # сказали только имя
        if not (text or "").strip():
            return False
        ear["text"] = text.strip()
        ear["ev"].set()
        self.emit("heard", text=ear["text"])
        return True

    def _intro_level(self, v, src):
        if src != "mic":
            return
        ear = self._intro_ear
        if ear is not None and v > ear["peak"]:
            ear["peak"] = v
        pr = self._intro_probe                          # пока знакомство говорит — есть ли у микрофона хоть какой-то шум
        if pr is not None:
            pr["n"] += 1
            pr["peak"] = max(pr["peak"], v)

    def _mic_absent(self):
        """Микрофона нет: ни одного входного устройства — или уровень всё время ровно ноль (у живого микрофона всегда есть
        шум; «цифровая тишина» — устройство есть, но в нём ничего нет, как «штатный» вход без подключённого микрофона)."""
        try:
            from audio_io import list_devices
            devs = list_devices()
            if devs and not any(d.get("kind") == "source" for d in devs):
                return True
        except Exception:  # noqa: BLE001
            pass
        pr = self._intro_probe or {}
        return pr.get("n", 0) >= 10 and pr.get("peak", 0.0) <= 0.0015

    # ---------- сам сценарий ----------

    def _intro_say(self, text, step, **extra):
        """Сказать фразу голосом персонажа и дождаться конца. -> False, если знакомство прервали."""
        if getattr(self, "_intro_stop", False):
            return False
        text = self._g(text)
        self.emit("intro", step=step, text=text, **extra)
        with self._turn_lock:
            self._cancel_locked()
            self.turn_n += 1
            turn = Turn(self.turn_n)
            self.turn = turn
        import queue
        q = queue.Queue()
        q.put(text)
        q.put(None)
        self.emit("answer", text=text, done=True)
        try:
            self._speak_worker(turn, q)
        except Exception as e:  # noqa: BLE001
            log.warning("знакомство: не сказала «%s»: %s", text[:40], e)
        with self._turn_lock:
            if self.turn is turn:
                self.turn = None
        if self.state == "speaking":
            self.set_state("idle")
        return not getattr(self, "_intro_stop", False) and not turn.cancel.is_set()

    def _intro_listen(self, seconds=None):
        """Ждать фразу без имени seconds секунд. -> (текст или "", пик громкости)."""
        seconds = seconds or self._intro_wait_s
        ear = {"ev": threading.Event(), "text": "", "peak": 0.0}
        self._intro_ear = ear
        self.follow_up_until = time.monotonic() + seconds
        self.set_state("listening")
        self.play_tone("wake")
        ear["ev"].wait(seconds)
        if not ear["text"] and ear["peak"] > 0.04 and not getattr(self, "_intro_stop", False):
            ear["ev"].wait(min(4.0, seconds))         # голос был — дадим распознавателю договорить
        self._intro_ear = None
        self.follow_up_until = 0.0
        if self.state == "listening":
            self.set_state("idle")
        return ear["text"], ear["peak"]

    def _intro_run(self):
        self._intro_stop = False
        try:
            self._intro_script()
        except Exception as e:  # noqa: BLE001
            log.warning("знакомство прервалось: %s", e)
        finally:
            self._intro_ear = None
            self._intro_probe = None
            self._intro_running = False
            if (self.s.get("ui", "intro") or "") != "done":
                self.s.update({"ui": {"intro": "done"}})
            self.emit("intro", step="done")

    def _intro_script(self):
        pid = self.s.get("persona")
        p = self.personas.get(pid)
        name = p["name"]
        log.info("👋 Знакомство: %s", name)
        self.emit("intro", step="start", name=name)
        try:
            self.ensure_voice(pid)                    # голос скачивается при первом запуске — до первой фразы
        except Exception as e:  # noqa: BLE001
            log.debug("знакомство: голос: %s", e)
        self._intro_probe = {"n": 0, "peak": 0.0}
        if not self._intro_say(f"Привет! Я {name}. Я живу вот в этом окошке: слышу тебя, говорю с тобой и помогаю "
                               "с делами — от напоминаний до презентаций.", "hello"):
            return
        # --- микрофон ---
        hearing = getattr(self, "_audio_thread", None) is not None and not self.muted
        no_mic = hearing and self._mic_absent()
        self._intro_probe = None
        if no_mic:                                       # говорить некуда — не просим, а показываем, как писать
            if not self._intro_say("Микрофона я не слышу совсем — похоже, он не подключён. Ничего страшного: мне можно "
                                   "писать — кнопка с клавиатурой внизу окошка. Подключишь микрофон — просто позови меня по имени.",
                                   "mic_none", buttons=["b-type"]):
                return
        elif hearing:
            if not self._intro_say(f"Давай проверим микрофон. Скажи что-нибудь — например: «Привет, {name}!»", "mic",
                                   phrase=f"Привет, {name}!"):
                return
            text, peak = self._intro_listen()
            if getattr(self, "_intro_stop", False):
                return
            if text:
                ok = self._intro_say(f"Слышу тебя отлично: «{text.rstrip('.!?')}». Микрофон в порядке.", "mic_ok", heard=text)
            elif peak > 0.04:
                ok = self._intro_say("Голос слышу, но слов не разобрать. Говори чуть ближе к микрофону или громче — "
                                     "настроить можно в студии, в разделе «Слух».", "mic_weak")
            else:
                ok = self._intro_say("Тебя пока не слышно. Проверь, тот ли микрофон выбран: студия, раздел «Звук и поведение». "
                                     "А пока можно писать — кнопка с клавиатурой внизу.", "mic_fail")
            if not ok:
                return
        else:
            if not self._intro_say("Микрофон сейчас выключен — включишь кнопкой с микрофоном внизу окошка.", "mic_off"):
                return
        # --- кнопки виджета ---
        if not self._intro_say("Внизу окошка — кнопки. Микрофон — выключить слух. Динамик — отвечать только текстом. "
                               "Клавиатура — написать вопрос. А само окошко можно перетащить куда удобно.", "buttons",
                               buttons=["b-mic", "b-voice", "b-type"]):
            return
        # --- мозг и что попробовать ---
        if not self.s.active_llm():
            self._intro_say("Чтобы думать и отвечать, мне нужен мозг. Выбери любой: модель на этом компьютере, ключ "
                            "любимого сервиса — OpenAI или Claude — или попробуй Dewiar по заявке. Раздел «Модели» в студии я открыл.",
                            "brain", page="brain")
            return
        tries = [f"{name}, который час?", "Напомни через пять минут проверить чайник", f"{name}, что ты умеешь?"]
        self._intro_say(f"Попробуй сказать: «{tries[0]}», «{tries[1]}» или «{tries[2]}». Зови меня по имени — "
                        "и я отвечу.", "try", phrases=tries)
