import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// Load-options dialog shown after the user picks a template file — but
// only when that file bundles stage positions (the page checks first via
// cryoActivities.template_position_count). Lets the user decide whether
// to import the bundled positions; on accept the page calls
// load_template(fileUrl, loadPositions).
//
// Public API:
//   • fileUrl (url)            — the chosen template, carried through to
//                                the page's accept handler.
//   • positionCount (int)      — number of bundled positions, for the label.
//   • loadPositions (readonly) — the checkbox state.

Dialog {

    id: root

    // --- Public API ---
    property url fileUrl
    property int positionCount: 0
    readonly property bool loadPositions: loadCheck.checked

    // --- Visuals (match ConfirmDialog chrome) ---
    title: Strings.templateLoadOptionsTitle
    modal: true
    anchors.centerIn: parent
    width: AppConfig.dialogDefaultWidth
    padding: AppConfig.dialogPadding

    background: Rectangle {
        color: AppConfig.activityContainerBackground
        radius: AppConfig.activityContainerRadius
        border.color: AppConfig.activityContainerIdleBorder
        border.width: AppConfig.activityContainerBorderWidth
    }

    header: Label {
        text: root.title
        font.pixelSize: AppConfig.activityTitleFontSize
        font.bold: true
        color: AppConfig.universalForeground
        padding: AppConfig.dialogPadding
        background: Rectangle { color: AppConfig.activityContainerBackground }
    }

    // --- Content ---
    contentItem: ColumnLayout {
        spacing: AppConfig.pageSectionSpacing

        CheckBox {
            id: loadCheck
            checked: true
            text: Strings.templateLoadPositionsLabel
                  + " (" + root.positionCount + ")"
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            // Wrap rather than elide a long label (see the save dialog).
            contentItem: Text {
                text: loadCheck.text
                font: loadCheck.font
                color: AppConfig.universalForeground
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                leftPadding: loadCheck.indicator.width + loadCheck.spacing
            }
        }

        Label {
            text: Strings.templateLoadPositionsHint
            font.pixelSize: AppConfig.pageBodyFontSize
            color: AppConfig.universalForeground
            wrapMode: Label.WordWrap
            Layout.fillWidth: true
        }
    }

    // --- Footer (Cancel / Load) ---
    footer: DialogButtonBox {
        alignment: Qt.AlignRight
        background: Rectangle { color: "transparent" }

        Button {
            text: Strings.dialogDefaultRejectText
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
        Button {
            text: Strings.templateLoadContinueText
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
    }
}
