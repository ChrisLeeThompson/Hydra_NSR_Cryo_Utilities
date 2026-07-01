import QtQuick
import "../Config"

// Hover Outline
//
// Shared accent outline shown when a clickable element is hovered.
// Used by the diagram's tick and beam labels (as their background) and
// by preset buttons (as an overlay child), keeping the hover treatment
// visually consistent across the app.
//
// Label usage (as background):
//     background: HoverOutline { hovered: labelMA.containsMouse }
//
// Button usage (as overlay child; match the button's corner radius):
//     RoundButton {
//         id: presetButton
//         ...
//         HoverOutline {
//             anchors.margins: 0
//             radius: presetButton.radius
//             hovered: presetButton.hovered
//         }
//     }

Rectangle {

    id: root

    property bool hovered: false

    anchors.fill: parent
    anchors.margins: -3
    color: "transparent"
    border.color: root.hovered ? AppConfig.universalAccent : "transparent"
    border.width: 1
    radius: 3

}
