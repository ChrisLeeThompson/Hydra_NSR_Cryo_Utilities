import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"
import "../Effects"

// Parameterized confirmation dialog used for "Move To",
// "Update Position", and "Remove Position", and for the two
// pre-start check dialogs hosted in main.qml. Emits the
// Dialog-standard accepted / rejected signals — no custom payload
// needed because the caller already knows which row / operation is
// under consideration.
//
// Two content modes, selected by which property the caller sets:
//
//   • message (string)    — a single wrapped Label. Used by the
//                           position dialogs and the generic error
//                           dialog.
//
//   • checkItems (list)   — one row per pre-start check result.
//                           Each row renders the warning icon
//                           (same asset and exception color as the
//                           activity-container status icon) with
//                           the result's bold title and message to
//                           the right. The per-row icon plays the
//                           role a bullet would — multiple results
//                           stack without any list markup. Each
//                           entry is an object with `title` and
//                           `message` string fields; either may be
//                           empty (the corresponding label
//                           collapses), produced by
//                           pre_start_checks.orchestrator.to_dialog_items.
//
// checkItems takes precedence when non-empty; the message Label is
// excluded from layout in that case. The two pre-start dialog
// instances in main.qml only ever set checkItems, and the position
// dialogs only ever set message, so the modes don't interleave in
// practice — precedence is defined for determinism, not as a
// feature.
//
// Usage pattern (instantiate in the parent, call open on click):
//
//     ConfirmDialog {
//         id: removeDialog
//         title: "Remove Position"
//         onAccepted: { /* pop the row */ }
//     }
//
//     // from a button's onClicked:
//     removeDialog.message = "Remove \"Position 1\" from the list?"
//     removeDialog.open()
//
//     // pre-start check mode (from a Connections handler):
//     preStartRefuseDialog.checkItems = items
//     preStartRefuseDialog.open()

Dialog {

    id: root

    // --- Public API ---
    property string message: ""
    // Pre-start check results: a JS array of { title, message }
    // objects. Non-empty switches the content area to icon rows.
    property var checkItems: []
    property bool destructive: false
    // When true, the Cancel button is hidden — leaves only OK as a
    // dismissal action. Useful for informational / error dialogs that
    // don't represent a yes/no choice.
    property bool dismissOnly: false
    // Button labels. Defaults are sourced from Strings so all dialog
    // wording lives in one place; the values are unchanged ("Ok" /
    // "Cancel"). Override on a per-instance basis where a richer action
    // verb fits — see the pre-start-check acknowledgement dialog in
    // main.qml, which sets acceptText to Strings.preStartConfirmAcceptText.
    property string acceptText: Strings.dialogDefaultAcceptText
    property string rejectText: Strings.dialogDefaultRejectText

    // True when the caller supplied check items — drives the
    // content-mode switch below. Defensive `&&` guard so an
    // accidental undefined/null assignment falls back to message
    // mode rather than throwing on `.length`.
    readonly property bool _checkMode: !!root.checkItems
                                       && root.checkItems.length > 0

    // --- Visuals ---
    modal: true
    anchors.centerIn: parent
    width: AppConfig.dialogDefaultWidth
    padding: AppConfig.dialogPadding

    // Dialog body background. Same color throughout so the header
    // reads as continuous with the content area.
    background: Rectangle {
        color: AppConfig.activityContainerBackground
        radius: AppConfig.activityContainerRadius
        border.color: AppConfig.activityContainerIdleBorder
        border.width: AppConfig.activityContainerBorderWidth
    }

    // Custom header with matching background so there's no dark
    // strip at the top of the dialog.
    header: Label {
        text: root.title
        font.pixelSize: AppConfig.activityTitleFontSize
        font.bold: true
        color: AppConfig.universalForeground
        padding: AppConfig.dialogPadding

        background: Rectangle {
            color: AppConfig.activityContainerBackground
        }
    }

    // --- Content ---
    //
    // A ColumnLayout hosting both modes; Layouts skip invisible
    // items, so exactly one mode contributes to the dialog's
    // implicit height at any time.
    contentItem: ColumnLayout {

        spacing: AppConfig.pageSectionSpacing

        // Mode 1: plain message (original behavior).
        Label {
            visible: !root._checkMode
            text: root.message
            font.pixelSize: AppConfig.pageBodyFontSize
            color: AppConfig.universalForeground
            wrapMode: Label.WordWrap
            Layout.fillWidth: true
            Layout.maximumHeight: 300
            elide: Label.ElideRight
        }

        // Mode 2: one icon + description row per check result.
        Repeater {
            model: root._checkMode ? root.checkItems : []

            delegate: RowLayout {

                id: checkRow
                required property var modelData

                Layout.fillWidth: true
                spacing: AppConfig.activityIconTitleGap

                // Warning icon — same asset, size, and exception
                // color as the activity-container status icon, so
                // the dialog speaks the visual vocabulary the user
                // already knows from failed activities.
                Item {
                    Layout.preferredWidth: AppConfig.activityStatusIconSize
                    Layout.preferredHeight: AppConfig.activityStatusIconSize
                    // Top-aligned so the icon sits beside the title
                    // line when the message wraps to several lines.
                    Layout.alignment: Qt.AlignTop

                    Image {
                        id: checkIconImage
                        anchors.fill: parent
                        source: AppConfig.iconWarning
                        sourceSize.width: width * 2
                        sourceSize.height: height * 2
                        fillMode: Image.PreserveAspectFit
                        visible: false
                    }

                    ColorOverlayEffect {
                        anchors.fill: checkIconImage
                        source: checkIconImage
                        colorOverlayColor: AppConfig.activityExceptionColor
                    }
                }

                // Title + message to the right of the icon. Either
                // label collapses when its text is empty (the
                // Python helper guarantees at least one is
                // non-empty per item).
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2

                    Label {
                        visible: text.length > 0
                        text: checkRow.modelData.title ?? ""
                        font.pixelSize: AppConfig.pageBodyFontSize
                        font.bold: true
                        color: AppConfig.universalForeground
                        wrapMode: Label.WordWrap
                        Layout.fillWidth: true
                    }

                    Label {
                        visible: text.length > 0
                        text: checkRow.modelData.message ?? ""
                        font.pixelSize: AppConfig.pageBodyFontSize
                        color: AppConfig.universalForeground
                        wrapMode: Label.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }
        }
    }

    // --- Footer (OK / Cancel) ---
    footer: DialogButtonBox {

        alignment: Qt.AlignRight
        background: Rectangle { color: "transparent" }

        Button {
            text: root.rejectText
            flat: true
            visible: !root.dismissOnly
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
            focus: root.destructive
        }

        Button {
            text: root.acceptText
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
            // When dismissOnly, OK is the only path — give it focus
            // regardless of `destructive`. Otherwise, focus follows the
            // existing rule (OK gets focus unless it's a destructive
            // confirm, in which case Cancel does).
            focus: root.dismissOnly || !root.destructive
        }
    }

}
