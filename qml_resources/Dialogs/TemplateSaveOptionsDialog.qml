import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// Save-options dialog shown before the native Save-File dialog on the
// Cryo Prep page. A native FileDialog can't host custom controls, so the
// "Include stage positions" choice lives here; on accept the page opens
// the file picker and then calls
// save_template(file, includePositions, scope).
//
// Option A layout: a master "Include stage positions" checkbox gates two
// nested radios for the scope. When the checkbox is off the radios are
// disabled and dimmed, so a user uninterested in positions reads a
// single line.
//
// Public API:
//   • includePositions (readonly bool)   — master checkbox state.
//   • scope (readonly string)            — "referenced" | "all"; only
//                                          meaningful when included.
//   • referencedCount / totalCount (int) — set by the caller before
//                                          open() to label the radios.

Dialog {

    id: root

    // --- Public API ---
    property int referencedCount: 0
    property int totalCount: 0
    readonly property bool includePositions: includeCheck.checked
    // Scope literals match templates.POSITION_SCOPE_REFERENCED / _ALL.
    readonly property string scope: allRadio.checked ? "all" : "referenced"

    // --- Visuals (match ConfirmDialog chrome) ---
    title: Strings.templateSaveOptionsTitle
    modal: true
    anchors.centerIn: parent
    width: AppConfig.dialogWideWidth
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
            id: includeCheck
            text: Strings.templateIncludePositionsLabel
            checked: true
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            // Override the default eliding contentItem so long labels
            // wrap to multiple lines inside the dialog instead of being
            // clipped. leftPadding clears the indicator, matching the
            // default style's layout.
            contentItem: Text {
                text: includeCheck.text
                font: includeCheck.font
                color: AppConfig.universalForeground
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
                leftPadding: includeCheck.indicator.width
                             + includeCheck.spacing
            }
        }

        // Nested scope radios — enabled only when positions are included.
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            spacing: 2
            enabled: includeCheck.checked
            opacity: includeCheck.checked ? 1.0 : 0.4

            RadioButton {
                id: referencedRadio
                checked: true
                text: Strings.templatePositionsReferencedLabel
                      + " (" + root.referencedCount + ")"
                font.pixelSize: AppConfig.pageBodyFontSize
                Layout.fillWidth: true
                contentItem: Text {
                    text: referencedRadio.text
                    font: referencedRadio.font
                    color: AppConfig.universalForeground
                    wrapMode: Text.WordWrap
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: referencedRadio.indicator.width
                                 + referencedRadio.spacing
                }
            }

            RadioButton {
                id: allRadio
                text: Strings.templatePositionsAllLabel
                      + " (" + root.totalCount + ")"
                font.pixelSize: AppConfig.pageBodyFontSize
                Layout.fillWidth: true
                contentItem: Text {
                    text: allRadio.text
                    font: allRadio.font
                    color: AppConfig.universalForeground
                    wrapMode: Text.WordWrap
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: allRadio.indicator.width + allRadio.spacing
                }
            }
        }
    }

    // --- Footer (Cancel / Continue) ---
    footer: DialogButtonBox {
        alignment: Qt.AlignRight
        background: Rectangle { color: "transparent" }

        Button {
            text: Strings.dialogDefaultRejectText
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }
        Button {
            text: Strings.templateSaveContinueText
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
    }
}
