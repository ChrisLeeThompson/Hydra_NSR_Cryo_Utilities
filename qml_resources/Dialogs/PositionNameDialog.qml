import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// Parameterized name-entry dialog used for both "Add Position" and
// "Edit Position". Hosts a single TextField plus a validation-message
// Label that only appears when there's a problem.
//
// Validation strategy:
//   The validation label below the TextField is invisible when the
//   input is valid or empty. It only appears when the user has typed
//   a name that collides with an existing position — at that point
//   it reads "Name already exists." in the exception color.
//
//   The label's space is reserved even when invisible, so the dialog
//   height stays constant as the user types. An empty field doesn't
//   trigger a message — the disabled OK button communicates
//   "can't proceed yet" without needing words.
//
//   The OK button is disabled whenever the input isn't valid.
//
// Usage pattern (instantiate in the parent, call open on click):
//
//     PositionNameDialog {
//         id: addDialog
//         title: "Add Position"
//         existingNames: [ "pos A", "pos B" ]
//         onAcceptedName: (name) => { /* add to model */ }
//     }
//
//     // from a button's onClicked:
//     addDialog.existingNames = ...
//     addDialog.initialName = ""
//     addDialog.open()

Dialog {

    id: root

    // --- Public API ---

    // List of names that must not be re-used. Typically harvested by
    // the caller from the positions model.
    property var existingNames: []

    // Pre-fill for the Edit case. Leave empty for Add.
    property string initialName: ""

    // --- Signals ---
    signal acceptedName(string name)

    // --- Derived validation state ---
    readonly property string _trimmedName: nameField.text.trim()
    readonly property bool _nameEmpty: _trimmedName === ""
    readonly property bool _nameCollides:
        !_nameEmpty
        && _trimmedName !== initialName
        && existingNames.indexOf(_trimmedName) !== -1
    readonly property bool _nameValid: !_nameEmpty && !_nameCollides

    // --- Visuals ---
    modal: true
    anchors.centerIn: parent
    width: AppConfig.dialogDefaultWidth
    padding: AppConfig.dialogPadding

    // Dialog body background. Same color throughout so the header
    // reads as continuous with the content area (no dark strip).
    background: Rectangle {
        color: AppConfig.activityContainerBackground
        radius: AppConfig.activityContainerRadius
        border.color: AppConfig.activityContainerIdleBorder
        border.width: AppConfig.activityContainerBorderWidth
    }

    // Custom header — same background color as the body so the title
    // bar doesn't read as a separate zone. Title typography matches
    // the activity container title.
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

    // Reset the field to `initialName` each time the dialog opens.
    // Using onAboutToShow (not onOpened) means the field is already
    // correct when the first frame paints — no flicker of stale text.
    onAboutToShow: {
        nameField.text = initialName
        nameField.forceActiveFocus()
        nameField.selectAll()
    }

    // --- Content ---
    contentItem: ColumnLayout {
        spacing: 6

        Label {
            text: "Name"
            font.pixelSize: AppConfig.pageBodyFontSize
            color: AppConfig.universalForeground
            Layout.alignment: Qt.AlignLeft
        }

        TextField {
            id: nameField
            Layout.fillWidth: true
            placeholderText: Strings.stagePositionNamePlaceholderText
            placeholderTextColor: AppConfig.placeholderTextColor
            selectByMouse: true

            // No custom background — fall back to the Universal
            // theme's default underline-style input treatment so the
            // field matches other input controls in the app.

            // Enter accepts when valid.
            Keys.onReturnPressed: if (root._nameValid) root.accept()
            Keys.onEnterPressed: if (root._nameValid) root.accept()
        }

        // Validation message — only appears when the typed name
        // collides with an existing one. Space is reserved at all
        // times so the dialog height is constant. Empty-field state
        // is not flagged here; the disabled OK button is enough.
        Label {
            id: validationLabel
            Layout.fillWidth: true
            text: Strings.stagePositionNameCollisionError
            font.pixelSize: AppConfig.pageBodyFontSize - 2
            color: AppConfig.activityExceptionColor
            wrapMode: Label.WordWrap
            // `opacity` rather than `visible` so the label still
            // participates in layout and the dialog doesn't resize.
            opacity: root._nameCollides ? 1.0 : 0.0
            Behavior on opacity {
                NumberAnimation { duration: 120 }
            }
        }
    }

    // --- Footer (OK / Cancel) ---
    footer: DialogButtonBox {

        alignment: Qt.AlignRight
        background: Rectangle { color: "transparent" }

        Button {
            text: "Cancel"
            flat: true
            DialogButtonBox.buttonRole: DialogButtonBox.RejectRole
        }

        Button {
            text: "OK"
            flat: true
            enabled: root._nameValid
            DialogButtonBox.buttonRole: DialogButtonBox.AcceptRole
        }
    }

    // Fire the typed signal on accept. `accepted` (the built-in
    // Dialog signal) still fires too — consumers can listen to either.
    onAccepted: root.acceptedName(root._trimmedName)

}
