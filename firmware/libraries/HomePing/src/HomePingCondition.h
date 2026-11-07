// A condition that has to hold for a while before it counts, with hysteresis (#294, #306):
// "above 5 °C for 30 min" on the fridge node; the planned outdoor probe will want the same.
// Header-only, no dependencies.
#pragma once

// Whether the condition holds, and since when (millis(); the unsigned subtraction in
// lasted() stays right across the 49.7-day wrap). The caller passes both thresholds, so
// the hysteresis is explicit: e.g. enter above 5.0, leave at or below 4.5.
struct HpCondition {
  bool on = false;
  unsigned long since = 0;

  // Call with every reading: [enter] starts it (the timer from [now]), [leave] ends it.
  void track(bool enter, bool leave, unsigned long now) {
    if (!on && enter) { on = true; since = now; }
    else if (on && leave) on = false;
  }

  // Whether it holds and has for at least [ms].
  bool lasted(unsigned long ms, unsigned long now) const {
    return on && now - since >= ms;
  }
};
