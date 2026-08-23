
import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"
import "../Effects"

// Generic activity container with:
//   • Full-height vertical drag handle on the left edge (optional).
//   • Expand/collapse chevron + status icon + title + parameter summary,
//     with the whole region acting as a single click target that
//     toggles `expanded`.
//   • Slot for header-extra content (e.g. delete button).
//   • Switch (optional).
//   • Collapsible body via `contentData`.

Rectangle {

    id: root

    // --- Identification ---
    property string title: ""
    property string titleToolTip: ""
    property string parameterSummary: ""

    // --- State ---
    property string activityState: "idle"      // idle | running | complete | exception
    property string statusMessage: ""

    // --- Switch ---
    property bool switchChecked: false
    property bool switchEnabled: true
    property bool switchVisible: true

    // --- Optional features ---
    property bool dragHandleVisible: false
    // Independent of dragHandleVisible: when false, the handle still
    // occupies its column (so the body's width doesn't shift) but is
    // visually dimmed and ignores hover/drag input. Used by pages that
    // need to lock reorder during a workflow run without causing a
    // layout reflow.
    property bool dragHandleEnabled: true
    // When true, the title and parameter summary stack vertically
    // (title on top, summary below) instead of running inline on a
    // single row. Per-call-site choice — set to true for activity
    // containers where the parameter summary is long enough that
    // inline display elides aggressively. Default preserves the
    // original inline layout. The structural switch happens via a
    // Loader; see headerLoader below.
    property bool stackHeader: false
    property bool expanded: false              // default: collapsed
    // When false, the chevron is hidden, the header click-to-toggle
    // becomes a no-op, and the body is always shown regardless of
    // `expanded`. Use for static surfaces (e.g. the Lift-out
    // Calculator) that borrow the activity container's visual chrome
    // but have no idle/expanded state to toggle.
    property bool collapsible: true
    // When false, the status icon slot is hidden and collapses to zero
    // width, letting the title slide flush against the chevron column
    // (or the left edge when chevron is also hidden). Use for surfaces
    // that have no activity state to communicate. Independent of
    // `collapsible`: a non-collapsible container can still meaningfully
    // show a status icon (e.g. a static connection-status banner).
    property bool statusIconVisible: true

    // --- Composition slots ---
    default property alias contentData: contentArea.data
    property alias headerExtraData: headerExtraArea.data

    // --- Drag wiring (set by the page when the container participates in a reorderable list) ---
    readonly property alias dragHandleMouseArea: dragHandleMA

    // --- Signals ---
    signal toggled(bool enabled)

    // --- Derived state ---
    readonly property bool _hasContent: contentArea.children.length > 0
    // The header row collapses entirely when it has nothing to show:
    // no title, no parameter summary, no chevron (collapsible: false),
    // no status icon, no switch, and no header-extra content. Containers
    // used as pure framing (e.g. the figure containers on the Milling
    // Angle Calc page) set title: "" alongside those flags and reclaim
    // the header space for the body.
    readonly property bool _headerVisible: title !== ""
                                           || parameterSummary !== ""
                                           || collapsible
                                           || statusIconVisible
                                           || switchVisible
                                           || headerExtraArea.children.length > 0
    // Body is open when the container has content AND either the user
    // has expanded it OR collapsing is disabled (static surface).
    readonly property bool _bodyOpen: root._hasContent
                                      && (root.expanded || !root.collapsible)

    // ------------------------------------------------------------------
    // Visuals
    // ------------------------------------------------------------------

    color: AppConfig.activityContainerBackground
    radius: AppConfig.activityContainerRadius
    border.width: AppConfig.activityContainerBorderWidth

    border.color: {
        switch (activityState) {
            case "running":   return AppConfig.activityRunningColor
            case "complete":  return AppConfig.activityCompleteColor
            case "exception": return AppConfig.activityExceptionColor
            default:          return AppConfig.activityContainerIdleBorder
        }
    }

    Behavior on border.color {
        ColorAnimation { duration: AppConfig.activityBorderColorDurationMs }
    }

    implicitHeight: fullColumn.implicitHeight + AppConfig.activityContainerPadding * 2

    // ------------------------------------------------------------------
    // Drag handle (full-height vertical bar, left edge)
    // ------------------------------------------------------------------

    Rectangle {

        id: dragHandle
        visible: root.dragHandleVisible

        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.leftMargin: AppConfig.activityContainerPadding
        anchors.topMargin: AppConfig.activityContainerPadding
        anchors.bottomMargin: AppConfig.activityContainerPadding

        width: AppConfig.activityDragHandleWidth
        radius: 2

        color: dragHandleMA.containsMouse || dragHandleMA.drag.active
               ? AppConfig.universalAccent
               : AppConfig.activityDragHandleColor
        // Opacity rule:
        //   • disabled  → 0.25 (greyed-out / decorative)
        //   • hovered or being dragged → 1.0
        //   • idle (visible, enabled, neither hovered nor dragged) → 0.6
        // The MouseArea is disabled when !dragHandleEnabled, so
        // containsMouse / drag.active will already stay false — but
        // we still need this branch to override the resting 0.6 so
        // the handle reads as inert.
        opacity: !root.dragHandleEnabled
                 ? 0.25
                 : (dragHandleMA.containsMouse || dragHandleMA.drag.active
                    ? 1.0
                    : 0.6)

        Behavior on color { ColorAnimation { duration: 120 } }
        Behavior on opacity { NumberAnimation { duration: 120 } }

        MouseArea {
            id: dragHandleMA
            anchors.fill: parent
            anchors.margins: -6                 // extend hit area ±6 px
            // When disabled: no hover events, no cursor change, no
            // drag — the handle is fully inert. Layout space is
            // preserved by the parent Rectangle still being visible.
            enabled: root.dragHandleEnabled
            hoverEnabled: true
            cursorShape: Qt.SizeVerCursor
            drag.axis: Drag.YAxis
        }
    }

    // ------------------------------------------------------------------
    // Main content column (header + body)
    // ------------------------------------------------------------------

    ColumnLayout {

        id: fullColumn

        anchors.top: parent.top
        anchors.right: parent.right
        anchors.left: parent.left
        anchors.topMargin: AppConfig.activityContainerPadding
        anchors.rightMargin: AppConfig.activityContainerPadding
        // Leave room for the drag handle when visible
        anchors.leftMargin: root.dragHandleVisible
                            ? AppConfig.activityContainerPadding
                              + AppConfig.activityDragHandleWidth
                              + AppConfig.activityContainerSpacing
                            : AppConfig.activityContainerPadding

        spacing: root._bodyOpen ? AppConfig.activityContainerSpacing : 0
        Behavior on spacing {
            NumberAnimation {
                duration: AppConfig.activityExpandDurationMs
                easing.type: Easing.InOutQuad
            }
        }

        // ---- Header row ----
        RowLayout {

            id: headerRow
            visible: root._headerVisible
            Layout.fillWidth: true
            Layout.minimumHeight: AppConfig.activityContainerHeaderHeight
            Layout.preferredHeight: Math.max(implicitHeight,
                                             AppConfig.activityContainerHeaderHeight)

            spacing: AppConfig.activityContainerSpacing

            Item {

                id: expandClickArea

                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                Layout.preferredHeight: expandInnerRow.implicitHeight

                readonly property bool clickable: root._hasContent
                                                 && root.collapsible

                RowLayout {

                    id: expandInnerRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: AppConfig.activityIconTitleGap

                    // --- Chevron (first, flush-left) ---
                    Item {

                        id: chevronSlot
                        visible: expandClickArea.clickable
                        Layout.preferredWidth: expandClickArea.clickable
                                               ? AppConfig.activityChevronIconSize
                                               : 0
                        Layout.preferredHeight: AppConfig.activityChevronIconSize
                        Layout.alignment: Qt.AlignVCenter

                        rotation: root.expanded ? 90 : 0
                        Behavior on rotation {
                            NumberAnimation {
                                duration: AppConfig.activityChevronRotateDurationMs
                                easing.type: Easing.InOutQuad
                            }
                        }

                        Image {
                            id: chevronImage
                            anchors.fill: parent
                            source: AppConfig.iconChevronRight
                            sourceSize.width: width * 2
                            sourceSize.height: height * 2
                            fillMode: Image.PreserveAspectFit
                            visible: false
                        }

                        ColorOverlayEffect {
                            anchors.fill: chevronImage
                            source: chevronImage

                            colorOverlayColor: expandHover.hovered
                                               ? AppConfig.universalAccent
                                               : AppConfig.universalForeground
                            Behavior on colorOverlayColor {
                                ColorAnimation { duration: 120 }
                            }
                        }
                    }

                    // --- Status icon ---
                    Item {

                        id: statusIconSlot
                        visible: root.statusIconVisible
                        Layout.preferredWidth: root.statusIconVisible
                                               ? AppConfig.activityStatusIconSize
                                               : 0
                        Layout.preferredHeight: AppConfig.activityStatusIconSize
                        Layout.alignment: Qt.AlignVCenter

                        HoverHandler { id: statusIconHover }

                        // Explicit ToolTip (not the attached form) so the
                        // text wraps: exception details arrive as a
                        // second line and can run long.
                        ToolTip {
                            id: statusToolTip
                            text: root.statusMessage
                            visible: statusIconHover.hovered
                                     && root.statusMessage !== ""
                                     && root.activityState !== "idle"
                                     && root.activityState !== "running"
                            delay: AppConfig.toolTipDelayMs
                            contentItem: Text {
                                text: statusToolTip.text
                                font: statusToolTip.font
                                color: statusToolTip.palette.toolTipText
                                wrapMode: Text.Wrap
                            }
                            width: Math.min(
                                implicitWidth, AppConfig.toolTipMaxWidth
                            )
                        }

                        BusyIndicator {
                            id: busyIndicator
                            anchors.fill: parent
                            running: root.activityState === "running"
                            visible: running
                        }

                        Image {
                            id: statusIconImage
                            anchors.fill: parent
                            fillMode: Image.PreserveAspectFit
                            sourceSize.width: width * 2
                            sourceSize.height: height * 2
                            visible: false
                            source: {
                                switch (root.activityState) {
                                    case "complete":  return AppConfig.iconCheckmark
                                    case "exception": return AppConfig.iconWarning
                                    default:          return ""
                                }
                            }
                        }

                        ColorOverlayEffect {
                            anchors.fill: statusIconImage
                            source: statusIconImage
                            visible: statusIconImage.source !== ""
                                     && !busyIndicator.running
                            colorOverlayColor: {
                                switch (root.activityState) {
                                    case "complete":  return AppConfig.activityCompleteColor
                                    case "exception": return AppConfig.activityExceptionColor
                                    default:          return "transparent"
                                }
                            }
                        }
                    }

                    // --- Title + parameter summary ---
                    //
                    // Two layout choices, picked by root.stackHeader:
                    //
                    //   • inlineHeaderComponent (default) — title and
                    //     summary on a single row, summary elides
                    //     aggressively when the row is narrow.
                    //
                    //   • stackedHeaderComponent — title above
                    //     summary, both filling the column's width
                    //     and eliding independently.
                    //
                    // Loader instantiates only the active component
                    // so the inactive layout's bindings don't run.
                    // The chevron and status icon stay flush-left in
                    // the parent RowLayout regardless of which header
                    // shape is active.
                    Loader {
                        id: headerLoader
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignVCenter
                        sourceComponent: root.stackHeader
                                         ? stackedHeaderComponent
                                         : inlineHeaderComponent
                    }

                    Component {
                        id: inlineHeaderComponent

                        // Inline layout: the original shape preserved
                        // exactly. Title takes its natural width, the
                        // summary fills remaining space and elides.
                        // RowLayout (rather than Row) so Layout.*
                        // properties work correctly when the loaded
                        // item is parented into the outer RowLayout.
                        RowLayout {
                            spacing: 0

                            Label {
                                id: inlineTitleLabel
                                Layout.alignment: Qt.AlignVCenter
                                text: root.title
                                font.pixelSize: AppConfig.activityTitleFontSize
                                elide: Label.ElideRight

                                ToolTip.text: root.titleToolTip
                                ToolTip.visible: root.titleToolTip !== ""
                                                 && expandHover.hovered
                                ToolTip.delay: AppConfig.toolTipDelayMs
                                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                            }

                            Label {
                                id: inlineSummaryLabel
                                Layout.alignment: Qt.AlignVCenter
                                Layout.fillWidth: true
                                text: "  " + root.parameterSummary
                                font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                color: AppConfig.activityParameterSummaryColor
                                elide: Label.ElideRight

                                ToolTip.text: root.parameterSummary
                                ToolTip.visible: expandHover.hovered
                                                 && inlineSummaryLabel.truncated
                                ToolTip.delay: AppConfig.toolTipDelayMs
                                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                            }
                        }
                    }

                    Component {
                        id: stackedHeaderComponent

                        // Stacked layout: title row on top, summary
                        // row below. Both labels fill the column's
                        // width so each can elide independently.
                        ColumnLayout {
                            spacing: AppConfig.activityStackHeaderColumnSpacing

                            Label {
                                id: stackedTitleLabel
                                Layout.fillWidth: true
                                text: root.title
                                font.pixelSize: AppConfig.activityTitleFontSize
                                elide: Label.ElideRight

                                ToolTip.text: root.titleToolTip
                                ToolTip.visible: root.titleToolTip !== ""
                                                 && expandHover.hovered
                                ToolTip.delay: AppConfig.toolTipDelayMs
                                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                            }

                            Label {
                                id: stackedSummaryLabel
                                Layout.fillWidth: true
                                text: root.parameterSummary
                                font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                color: AppConfig.activityParameterSummaryColor
                                elide: Label.ElideRight

                                ToolTip.text: root.parameterSummary
                                ToolTip.visible: expandHover.hovered
                                                 && stackedSummaryLabel.truncated
                                ToolTip.delay: AppConfig.toolTipDelayMs
                                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                            }
                        }
                    }
                }

                TapHandler {
                    id: expandTap
                    onTapped: {
                        if (expandClickArea.clickable) {
                            root.expanded = !root.expanded
                        }
                    }
                }
                HoverHandler {
                    id: expandHover
                }
            }

            // Header-extra slot (delete button, etc.)

            RowLayout {
                id: headerExtraArea
                Layout.alignment: Qt.AlignVCenter
                spacing: AppConfig.activityContainerSpacing
                visible: children.length > 0
            }

            // Switch
            Switch {
                id: activitySwitch
                Layout.alignment: Qt.AlignVCenter
                visible: root.switchVisible
                checked: root.switchChecked
                enabled: root.switchEnabled

                onToggled: {
                    root.switchChecked = checked
                    if (root.activityState === "complete"
                        || root.activityState === "exception") {
                        root.activityState = "idle"
                    }
                    root.toggled(checked)
                }
            }
        }

        // ---- Collapsible body ----
        Item {

            id: contentClipper
            Layout.fillWidth: true
            Layout.preferredHeight: root._bodyOpen ? contentArea.implicitHeight : 0
            clip: true
            visible: root._hasContent

            Behavior on Layout.preferredHeight {
                NumberAnimation {
                    duration: AppConfig.activityExpandDurationMs
                    easing.type: Easing.InOutQuad
                }
            }

            ColumnLayout {
                id: contentArea
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                // Content is enabled when:
                //   • the activity has no switch (switchVisible: false) —
                //     e.g. Stage Positions and Home Stage; content must
                //     always be interactive because there's no toggle
                //   • or the switch is checked
                // …AND the activity isn't currently running.
                enabled: (!root.switchVisible || root.switchChecked)
                         && root.activityState !== "running"
            }
        }
    }
}



