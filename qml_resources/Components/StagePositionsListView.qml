pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// Stage Positions list view.
//
// Renders the saved stage positions as a vertically-stacked list with
// expandable XYZTR sub-rows. The list view scrolls internally so a long
// list never inflates the activity container's height; the outer page
// scroll is independent.
//
// Interaction (matches v2.1):
//   • First click on a row          — selects it
//   • Second click on the same row  — toggles its XYZTR sub-rows
//
// Two-click UX means selection and expansion are independent — a user
// can browse selections (tab through positions to see which is which)
// without the UI snapping open/closed underneath them.
//
// Owned state:
//   • currentIndex         — which row is selected (propagates out
//                            via `selectedIndex` for the parent to
//                            read)
//   • each delegate's own `expanded` bool — local, not reflected into
//                            the model, because expansion is purely
//                            presentational
//
// Model schema: each element must expose `name`, `posX`, `posY`,
// `posZ`, `posT`, `posR`. The axis fields use the `pos` prefix because
// `x` and `y` collide with Item's built-in FINAL geometry properties —
// QML rejects `required property real x` outright with "Cannot override
// FINAL property". The page's `stagePositionsModel` in
// CryoTempPrepPage provides exactly this shape.

Item {

    id: root

    // --- Public API ---

    // Model supplied by the parent (StagePositions → page's
    // stagePositionsModel). `property var` rather than `property alias`
    // so the parent can rebind at runtime if the backend swap happens.
    property var model: null

    // Propagates the internal ListView's currentIndex out so the
    // parent can enable / disable action buttons based on selection.
    // -1 means "no row selected".
    readonly property alias selectedIndex: positionsListView.currentIndex

    // --- Visuals ---

    // A subtle bordered frame around the ListView so it reads as a
    // distinct region within the activity.
    Rectangle {
        anchors.fill: parent
        color: "transparent"
        border.color: AppConfig.activityContainerIdleBorder
        border.width: AppConfig.activityContainerBorderWidth
        radius: AppConfig.buttonRadius
    }

    // --- Delegate ---

    Component {

        id: positionDelegate

        Item {

            id: delegateRoot

            // Required properties for the model roles. Note the `pos`
            // prefix on the axis fields — see the module header for
            // why `x` and `y` can't be used here.
            required property int index
            required property string name
            required property real posX
            required property real posY
            required property real posZ
            required property real posT
            required property real posR

            // Local expansion state — independent of selection.
            property bool expanded: false

            // --- Inline components for the XYZTR sub-grid ---
            component AxisLabel : Label {
                font.pixelSize: AppConfig.pageBodyFontSize - 2
                color: AppConfig.universalForeground
            }

            component ValueLabel : Label {
                Layout.preferredWidth: AppConfig.activityStagePositionValueColumnWidth
                font.pixelSize: AppConfig.pageBodyFontSize - 2
                color: AppConfig.universalForeground
                horizontalAlignment: Text.AlignRight
            }

            width: ListView.view ? ListView.view.width : 0
            height: expanded
                    ? nameRow.implicitHeight + xyztrGrid.implicitHeight + 8
                    : nameRow.implicitHeight
            clip: true

            Behavior on height {
                NumberAnimation {
                    duration: AppConfig.activityExpandDurationMs
                    easing.type: Easing.InOutQuad
                }
            }

            // --- Selection highlight (full-width strip behind the name) ---
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: nameRow.implicitHeight
                color: positionsListView.currentIndex === delegateRoot.index
                       ? AppConfig.universalAccent
                       : "transparent"

                Behavior on color { ColorAnimation { duration: 120 } }
            }

            // --- Name row ---
            Item {

                id: nameRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                implicitHeight: 28

                Label {
                    id: nameLabel
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 8
                    anchors.rightMargin: 8
                    text: delegateRoot.name
                    font.pixelSize: AppConfig.pageBodyFontSize
                    color: AppConfig.universalForeground
                    elide: Label.ElideRight

                    // Full-name tooltip when elided.
                    ToolTip.text: delegateRoot.name
                    ToolTip.visible: nameLabelHover.hovered && nameLabel.truncated
                    ToolTip.delay: AppConfig.toolTipDelayMs
                    ToolTip.timeout: AppConfig.toolTipTimeoutMs

                    HoverHandler { id: nameLabelHover }
                }

                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        // Two-click UX: first click selects, second
                        // click on the same row toggles expansion.
                        if (positionsListView.currentIndex === delegateRoot.index) {
                            delegateRoot.expanded = !delegateRoot.expanded
                        } else {
                            positionsListView.currentIndex = delegateRoot.index
                        }
                    }
                }
            }

            // --- XYZTR sub-rows (visible when expanded) ---
            GridLayout {

                id: xyztrGrid
                anchors.left: parent.left
                anchors.top: nameRow.bottom
                anchors.topMargin: 4
                anchors.leftMargin: 24
                columns: 3
                rowSpacing: 2
                columnSpacing: 16
                visible: delegateRoot.expanded

                // Display conversions. The model holds AutoScript SI
                // units (meters, radians); this component is the only
                // place that does the m→mm and rad→deg conversion, so
                // any change here propagates to all callers.
                AxisLabel  { text: "X" }
                ValueLabel { text: (delegateRoot.posX * 1000).toFixed(4) }
                AxisLabel  { text: "mm" }

                AxisLabel  { text: "Y" }
                ValueLabel { text: (delegateRoot.posY * 1000).toFixed(4) }
                AxisLabel  { text: "mm" }

                AxisLabel  { text: "Z" }
                ValueLabel { text: (delegateRoot.posZ * 1000).toFixed(4) }
                AxisLabel  { text: "mm" }

                AxisLabel  { text: "T" }
                ValueLabel { text: (delegateRoot.posT * 180 / Math.PI).toFixed(4) }
                AxisLabel  { text: "°" }

                AxisLabel  { text: "R" }
                ValueLabel { text: (delegateRoot.posR * 180 / Math.PI).toFixed(4) }
                AxisLabel  { text: "°" }

            }
        }
    }

    // --- The list view itself ---
    ListView {
        id: positionsListView
        anchors.fill: parent
        anchors.margins: AppConfig.activityContainerBorderWidth
        clip: true
        model: root.model
        delegate: positionDelegate
        spacing: 0
        currentIndex: -1

        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
    }

}
