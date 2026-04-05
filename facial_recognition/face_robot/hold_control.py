"""Debounce faces + interaction -> Giga HOLD/RUN (same rules as robot_supervisor)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from face_robot.giga_link import GigaSerial


class HoldController:
    def __init__(
        self,
        link: Optional["GigaSerial"],
        present_streak: int,
        absent_streak: int,
        post_interaction_grace_sec: float,
        hold_pulse_sec: float = 0.0,
    ) -> None:
        self._link = link
        self._present_need = max(1, present_streak)
        self._absent_need = max(1, absent_streak)
        self._grace_sec = max(0.0, post_interaction_grace_sec)
        self._hold_pulse_sec = max(0.0, float(hold_pulse_sec))
        self._last_pulse_mono = 0.0
        self._streak_present = 0
        self._streak_absent = 0
        self._debounced_present = False
        self._grace_until = 0.0
        self._last_busy = False

    def tick(
        self,
        faces_count: int,
        interaction_busy: bool,
        *,
        resume_with_90_turn: bool = False,
    ) -> None:
        if self._link is None:
            return

        if faces_count > 0:
            self._streak_present += 1
            self._streak_absent = 0
        else:
            self._streak_absent += 1
            self._streak_present = 0

        if self._streak_present >= self._present_need:
            self._debounced_present = True
        if self._streak_absent >= self._absent_need:
            self._debounced_present = False

        busy = bool(interaction_busy)
        if self._last_busy and not busy:
            self._grace_until = time.monotonic() + self._grace_sec
        self._last_busy = busy

        now = time.monotonic()
        allow_hold_from_presence = now >= self._grace_until
        want_hold = busy or (allow_hold_from_presence and self._debounced_present)

        if want_hold:
            self._link.set_hold(True)
            if self._hold_pulse_sec > 0.0 and (now - self._last_pulse_mono) >= self._hold_pulse_sec:
                self._link.pulse_hold()
                self._last_pulse_mono = now
        else:
            self._link.set_hold(False, resume_with_90_turn=resume_with_90_turn)
            self._last_pulse_mono = 0.0

    def shutdown(self) -> None:
        if self._link is not None:
            self._link.close()
            self._link = None
