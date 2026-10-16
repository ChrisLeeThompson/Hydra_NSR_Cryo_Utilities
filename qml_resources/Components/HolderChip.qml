import QtQuick
import QtQuick.Controls
import "../Config"

// Holder Chip
//
// The tag beside a saved stage position naming the xT holder it was
// saved with: AG, ST, HPF, LS or SM (SUPPORTED_HOLDERS in defaults.py).
// Outline and text, fixed width so the chips line up down a list.
// White, or amber for the holders that ask for confirmation before a
// run or move selects them (HOLDERS_TO_CONFIRM). The fill is the
// activity panel's color, so the chip blends into the panel and stays
// legible on a selected row's highlight. Hovering shows the holder's
// xT name. A position with no recorded holder shows a dim "—" and a
// tooltip saying how to record one; a recorded holder outside the
// supported list (from an imported template) shows "?".
//
// Usage:
//     HolderChip {
//         holder: model.holder        // xT name; "" when not recorded
//         tag: model.holderTag        // "AG", "ST", ...; "" when none
//         needsConfirmation: model.holderConfirm
//     }

Rectangle {

    id: root

    // --- Public API ---
    property string holder: ""
    property string tag: ""
    // True for a holder in HOLDERS_TO_CONFIRM (ST, SM).
    property bool needsConfirmation: false

    readonly property bool _recorded: root.holder !== ""
    // Dim when no holder is recorded, and inside disabled content (an
    // activity whose switch is off) like the controls around it.
    readonly property color _ink: !root._recorded || !root.enabled
                                  ? AppConfig.textDisabledColor
                                  : root.needsConfirmation
                                    ? AppConfig.activityExceptionColor
                                    : AppConfig.universalForeground

    implicitWidth: AppConfig.holderChipWidth
    implicitHeight: AppConfig.holderChipHeight
    radius: 3
    // Matches the panel the chip sits on; backs the tag on a highlight.
    color: AppConfig.activityContainerBackground
    border.width: 1
    border.color: root._ink

    Label {
        anchors.centerIn: parent
        text: root._recorded ? (root.tag !== "" ? root.tag : "?") : "—"
        font.pixelSize: AppConfig.holderChipFontSize
        color: root._ink
    }

    HoverHandler { id: chipHover }

    ToolTip.text: root._recorded ? root.holder : Strings.holderNotRecordedTooltip
    ToolTip.visible: chipHover.hovered
    ToolTip.delay: AppConfig.toolTipDelayMs
    ToolTip.timeout: AppConfig.toolTipTimeoutMs

}
