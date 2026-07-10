pragma Singleton
import QtQuick

// Live, non-persisted UI state shared across pages.
//
// `compact` is the reactive "is the UI in its compact layout right now" flag.
// It is DERIVED, not stored: main.qml drives it via a Binding from the live
// window size (at or below the AppConfig compact breakpoints in either
// dimension). Every page reads it for its subtitle visibility and
// header-spacer height. Nothing may write `compact` except that one Binding.
//
// `compactRequested` is the request channel for the Settings-page "Compact
// Mode" checkbox: Settings emits it, main.qml snaps the window to the
// compact / normal size, and the resize then flips `compact` through the
// Binding — window size stays the single source of truth.
QtObject {
    property bool compact: false

    // on = true requests the compact snap size, false the normal size.
    signal compactRequested(bool on)
}
