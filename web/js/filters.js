/**
 * Shared filter-bar state.
 *
 * Slice 2's NL playlist builder will import this same module and drive the
 * constraint model from Claude's structured output, keeping a single source
 * of truth for "what the user is looking for."
 */

const _state = {
  genre: null,
  mood: null,
  year: null,
  artist: null,
  min_energy: null,
  max_energy: null,
};

const _listeners = [];

export const filters = {
  get() {
    return { ..._state };
  },

  set(updates) {
    for (const [k, v] of Object.entries(updates)) {
      _state[k] = v === "" ? null : v;
    }
    _listeners.forEach(fn => fn({ ..._state }));
  },

  clear() {
    for (const k of Object.keys(_state)) _state[k] = null;
    _listeners.forEach(fn => fn({ ..._state }));
  },

  onChange(fn) {
    _listeners.push(fn);
  },

  /** Returns only non-null entries, ready for api.tracks(). */
  toParams() {
    return Object.fromEntries(
      Object.entries(_state).filter(([, v]) => v != null)
    );
  },
};
