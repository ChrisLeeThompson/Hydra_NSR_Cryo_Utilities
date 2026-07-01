pragma Singleton
import QtQuick

// Live, non-persisted UI state shared across pages.
//
// `compact` is the reactive "is the UI in its compact layout right now" flag.
// It is DERIVED, not stored: main.qml drives it via a Binding from the live
// window size OR the persisted "Compact mode" checkbox override. Every page
// reads it for its subtitle visibility and header-spacer height, so the whole
// UI switches together as the window is resized past the compact breakpoint.
QtObject {
    property bool compact: false
}
