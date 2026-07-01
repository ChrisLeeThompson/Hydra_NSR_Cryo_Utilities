import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"

// Three-slot status bar:
//   • Left:   transient message (auto-clears via showMessage).
//   • Center: progress bar, shown when busy.
//   • Right:  persistent state indicator (e.g., connection status).

Rectangle {

    id: root

    // --- Public state ---
    property string message: ""            // Transient (left).
    property string statusIndicator: ""    // Persistent (right).
    property real progress: 0
    property bool indeterminate: false
    property bool busy: false

    // --- Public API (transient messages only) ---
    function showMessage(text, durationMs) {
        message = text
        messageTimer.interval = durationMs
        messageTimer.restart()
    }

    function clearMessage() {
        messageTimer.stop()
        message = ""
    }

    // --- Internal ---
    Timer {
        id: messageTimer
        repeat: false
        onTriggered: root.message = ""
    }

    color: AppConfig.universalBackground
    implicitHeight: 28

    RowLayout {

        anchors.fill: parent
        anchors.leftMargin: AppConfig.statusBarHorizontalMargin
        anchors.rightMargin: AppConfig.statusBarHorizontalMargin

        // Left — transient
        Label {
            id: transientLabel
            text: root.message
            font.pixelSize: AppConfig.statusBarLabelFontSize
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignLeft
        }

        // Center — progress
        ProgressBar {
            id: statusBarProgressBar
            Layout.preferredWidth: AppConfig.statusBarProgressWidth
            Layout.alignment: Qt.AlignHCenter
            opacity: root.busy ? 1 : 0
            value: root.progress
            indeterminate: root.indeterminate
            Behavior on opacity { NumberAnimation { duration: 150 } }
            Behavior on value { NumberAnimation { duration: 0 } }
        }

        // Right — persistent indicator
        Label {
            id: indicatorLabel
            text: root.statusIndicator
            font.pixelSize: AppConfig.statusBarLabelFontSize
            Layout.preferredWidth: AppConfig.statusBarIndicatorLabelWidth
            Layout.leftMargin: 6
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignRight
        }

    }

}
