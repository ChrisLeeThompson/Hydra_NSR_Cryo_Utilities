import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import "../Config"
import "../Components"
import "../Dialogs"

// Cryo Temperature Preparation Page.
//
// Composed of:
//   • A reorderable list of activities (Sputter Coat / GIS Deposition)
//     backed by appController.cryoActivities.model.
//   • A pinned Stage Positions header (the saved-positions activity).
//   • A pinned Home Stage footer.
//   • A button row: Restore Defaults, Add Sputter, Add GIS, Save,
//     Start, Stop.
//
// Drag-and-drop reorder uses the existing visual mechanics; on drop,
// the delegate calls appController.cryoActivities.move(from, to).
// The model emits the appropriate moveRows signals; the QML
// DelegateModel updates its visual order automatically.
//
// Per-row activity content:
//   The activity Loader binds its `instanceId` to the row's
//   model.instanceId, plus per-parameter values to the row's
//   per-parameter roles. The activity component's setters call
//   appController.cryoActivities.set_*(instanceId, value), which
//   updates the model row in place.
//
// Workflow wiring:
//
//   • Activity-status routing: cpWorkflow.activityStatusChanged
//     fires (activity_id, instance_id, status) for each activity
//     transition. The activity delegate routes on instance_id
//     (multiple Cryo instances may share an activity_id), while
//     the Home Stage footer routes on activity_id === "home_stage".
//     Containers keep their final state ("complete" / "stop" /
//     "exception") after a run for visual record, and reset to
//     "idle" only when the next workflow starts.
//
//   • Validation: cpWorkflow.validationFailed (instance_id,
//     message) fires before start when an activity's parameters
//     are invalid (e.g. a GIS Deposition referencing a deleted
//     position). The matching delegate sets its activityState to
//     "exception" and statusMessage to the error so the row's
//     status icon shows the warning and hovers display the reason.
//
//   • Switch state pushes from QML to Python (Pattern B): switch
//     toggles aren't persisted, so they don't have a Python source
//     of truth to two-way-bind to. The runner consults the current
//     enabled flag at the start of each activity, so toggling a
//     switch off mid-workflow opts that activity out of the run if
//     it hasn't started yet.
//
//   • Switch enable rule: a switch is enabled while no workflow is
//     running, OR while the activity is still in "idle" state during
//     a run (hasn't started yet). Once an activity has begun or
//     finished within a run, its switch locks until the workflow
//     ends. Mirrors RoomTempPrepPage.qml.
//
//   • On row removal: the delete button calls
//     cpWorkflow.forget_activity(instanceId) before
//     cryoActivities.remove_at(index) so the workflow's
//     enabled-set doesn't accumulate stale ids.
//
//   • Mid-run add / delete: as of the iterative-fetch runner,
//     adding a not-yet-started activity (and switching it on)
//     mid-run makes it pending — the runner picks it up at the
//     next fetch. Deleting a not-yet-started activity removes it
//     from subsequent fetches. The delete button is locked only
//     on the row that is currently running (its worker is mid-
//     execution against state we'd otherwise tear out from under
//     it); idle / complete / exception rows stay deletable. Both
//     add and delete are transparent to the existing UI; no extra
//     wiring needed beyond what's already here.
//
//   • Mid-run reorder: drag handles are hidden while the
//     workflow is running. The runner would technically handle
//     reorder fine (each iteration walks the model in current
//     order), but the UX is surprising — easier to lock until
//     a future explicit feature.
//
//   • Start / Stop drive cpWorkflow.start() / .stop().
//     Reset Parameters disables while a run is in progress.

Item {

    id: root

    // Disable when another page's workflow is running.
    //
    // We bind to the runningWorkflowId Property directly rather than
    // calling the is_page_blocked() slot. Property reads in QML
    // bindings trigger notify-signal tracking — function-call
    // results don't, so a slot-based binding wouldn't re-evaluate
    // when the running id changes.
    enabled: !appController.runningWorkflowId
             || appController.runningWorkflowId === "cryo_prep"

    // Activity container width policy — tracks the previous QML.
    readonly property int activityWidth:
        Math.min(activitiesListView.width, AppConfig.activityContainerMaxWidthCryoTemp)

    // Drag-and-drop bookkeeping (drop indicator state, computed in
    // the delegate's DropArea handlers).
    property Item dropTargetDelegate: null
    property string dropDirection: ""       // "above" | "below" | ""

    // ------------------------------------------------------------------
        // Drop indicator — a thin horizontal bar shown at the boundary
        // where a dragged activity will land when released. Parented to
        // the ListView's contentItem so it lives in the same coordinate
        // system as the delegates and scrolls with them.
        //
        // Position is computed imperatively in _refreshTargetY rather than
        // declaratively, because the bar's y depends on the chosen target
        // delegate's y plus a half-gap offset that's awkward to express
        // as a single binding. The imperative form also lets us hold the
        // bar's last position during fade-out (when dropTargetDelegate
        // goes null at drag-end) so the bar vanishes in place rather
        // than snapping to y=0.
        // ------------------------------------------------------------------
        Rectangle {

            id: dropIndicator

            z: 1000

            // Match the width and centering of the activity containers
            // themselves, so the line visually corresponds to the
            // activity it's positioning.
            width: root.activityWidth
            x: (activitiesListView.width - width) / 2
            height: 3
            radius: 1
            color: AppConfig.activitySeparatorColor

            // Visible only while a drop target is active. Fade in/out
            // mirroring the ListView's other transitions.
            opacity: root.dropTargetDelegate !== null ? 1.0 : 0.0
            Behavior on opacity {
                NumberAnimation { duration: 160; easing.type: Easing.OutQuad }
            }

            // Persistent y-coordinate for the bar. Updated imperatively
            // when target / direction change so that when the target
            // goes null on drag-end, targetY isn't reset — the bar
            // keeps its last position and the fade-out happens in place.
            property real targetY: 0
            y: targetY

            function _refreshTargetY() {
                if (!root.dropTargetDelegate)
                    return
                const t = root.dropTargetDelegate
                // Each item in the list (delegate, header, footer) is
                // laid out with an AppConfig.activityGap px bottom
                // padding strip that renders as the visible gap between
                // activities. The "boundary" (bottom of the upper item /
                // top of the lower item) sits at the bottom edge of that
                // gap — so to centre the bar in the gap we lift it by
                // gapSize / 2 before applying the usual half-height
                // centering offset.
                const gapSize = AppConfig.activityGap
                const boundary = t.y + (root.dropDirection === "below" ? t.height : 0)
                targetY = boundary - gapSize / 2 - height / 2
            }

            Connections {
                target: root
                function onDropTargetDelegateChanged() { dropIndicator._refreshTargetY() }
                function onDropDirectionChanged()     { dropIndicator._refreshTargetY() }
            }

            // Slide smoothly between drop targets while already visible;
            // the behavior is gated on opacity > 0.5 so the first
            // appearance snaps into place rather than sliding from
            // wherever the bar happened to be parked from the previous
            // drag.
            Behavior on y {
                enabled: dropIndicator.opacity > 0.5
                NumberAnimation { duration: 100; easing.type: Easing.OutQuad }
            }

            // Reparent to contentItem once the ListView is available, so
            // the bar lives in the scroll content's coordinate system.
            Component.onCompleted: {
                parent = activitiesListView.contentItem
            }
        }

    // ------------------------------------------------------------------
    // Header: Stage Positions activity (pinned at the top of the list)
    // ------------------------------------------------------------------
    Component {

        id: stagePositionsHeader

        Item {
            width: ListView.view ? ListView.view.width : 0
            height: stagePositionsContainer.implicitHeight + AppConfig.activityGap

            onHeightChanged: {
                if (stagePositionsContainer.expanded && ListView.view) {
                    ListView.view.positionViewAtBeginning()
                }
            }

            Connections {
                target: appController.stagePositions
                ignoreUnknownSignals: true
                function onMoveStarted() {
                    stagePositionsContainer.activityState = "running"
                }
                function onMoveFinished(success) {
                    stagePositionsContainer.activityState = "idle"
                }
            }

            ActivityContainer {
                id: stagePositionsContainer
                anchors.horizontalCenter: parent.horizontalCenter
                width: root.activityWidth
                title: "Stage Positions"
                titleToolTip: Strings.stagePositionsActivityTooltip
                switchVisible: false
                dragHandleVisible: false

                StagePositions { }
            }
        }
    }

    // ------------------------------------------------------------------
    // Footer: Home Stage activity (pinned at the bottom of the list)
    // ------------------------------------------------------------------
    Component {

        id: homeStageFooter

        Item {
            width: ListView.view ? ListView.view.width : 0
            height: homeStageContainer.implicitHeight + AppConfig.activityGap

            // Workflow → UI routing for the Home Stage activity.
            // Home Stage is single-instance, so we route on
            // activity_id rather than instance_id (the runner emits
            // an empty instance_id for single-instance activities).
            Connections {
                target: appController.cpWorkflow
                ignoreUnknownSignals: true

                function onWorkflowStarted() {
                    // Only reset if Home Stage is about to run; a completed,
                    // auto-disabled Home Stage keeps its badge until re-armed.
                    if (homeStageContainer.switchChecked) {
                        homeStageContainer.activityState = "idle"
                        homeStageContainer.statusMessage = ""
                    }
                }

                function onActivityStatusChanged(activity_id, instance_id, status) {
                    if (activity_id !== "home_stage") return
                    homeStageContainer.activityState = status

                    // Compose the status-icon tooltip via the runner's
                    // Q_INVOKABLE accessors. See RoomTempPrepPage.qml's
                    // matching handler for the rationale (one signal,
                    // pull data on demand, single source of truth for
                    // the duration format via formatDuration).
                    if (status === "complete") {
                        // Auto-disable on success (see the per-activity
                        // delegate handler above for the full rationale).
                        homeStageContainer.switchChecked = false
                        appController.cpWorkflow.set_home_stage_enabled(false)
                        const dur = appController.cpWorkflow.activityDuration(
                            activity_id, instance_id,
                        )
                        homeStageContainer.statusMessage = "Total duration: "
                            + appController.cpWorkflow.formatDuration(dur)
                    } else if (status === "exception") {
                        const msg = appController.cpWorkflow.lastStatusMessage(
                            activity_id, instance_id,
                        )
                        homeStageContainer.statusMessage = (msg !== "")
                            ? msg
                            : "Failed — see log for details"
                    }
                    // status === "stop": no icon, no tooltip.
                }
            }

            ActivityContainer {
                id: homeStageContainer
                anchors.horizontalCenter: parent.horizontalCenter
                width: root.activityWidth
                title: "Home Stage"
                titleToolTip: Strings.homeStageActivityTooltip
                dragHandleVisible: false
                // No content — chevron hides automatically.

                // `activityState` is referenced unqualified to avoid
                // a self-reference binding loop warning that QML
                // emits when a property's binding reads another
                // property on the same object via id.
                switchEnabled: !appController.cpWorkflow
                               || !appController.cpWorkflow.isRunning
                               || activityState === "idle"

                onToggled: function(enabled) {
                    if (appController.cpWorkflow) {
                        appController.cpWorkflow.set_home_stage_enabled(enabled)
                    }
                    // Re-arming clears the "complete" badge (see the
                    // per-activity delegate handler for rationale).
                    if (enabled) {
                        homeStageContainer.activityState = "idle"
                        homeStageContainer.statusMessage = ""
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Delegate for the reorderable activity rows.
    // ------------------------------------------------------------------
    Component {

        id: activityDelegate

        Item {

            id: delegateRoot

            // Roles consumed from the model. Required so QML errors
            // early if any go missing — better than silent undefineds.
            required property int index
            required property string instanceId
            required property string activityType
            required property string title

            // Per-activity-type parameter roles. Some are meaningless
            // for the "wrong" activity type (e.g. sputterPositionId on a
            // GIS Deposition row), but they're declared as required so
            // the delegate code doesn't need to special-case undefined
            // values. The model returns type-correct defaults for
            // irrelevant roles ("" / 0 / 0.0).
            required property string sputterPositionId
            required property int ionSpeciesIndex
            required property real ionCurrentA
            required property int sputterDuration
            required property int sputterChamberRecovery

            required property string positionId
            required property int gisDuration
            required property int gisChamberRecovery

            readonly property string titleToolTip: {
                switch (activityType) {
                    case "sputter_coat":   return Strings.sputterCoatActivityTooltip
                    case "gis_deposition": return Strings.gisDepositionActivityTooltip
                    default:               return ""
                }
            }

            // Per-activity-type content Components. Defined inside the
            // delegate (rather than at page scope) so they capture the
            // delegate's binding context, including the row's `model`
            // and the delegateRoot's required properties. A page-level
            // Component would not see `model` at all and the activity
            // content would silently fail to render.
            Component {
                id: sputterContentComponent
                SputterCoat {
                    instanceId: delegateRoot.instanceId
                    positionId: delegateRoot.sputterPositionId
                    ionSpeciesIndex: delegateRoot.ionSpeciesIndex
                    ionCurrentA: delegateRoot.ionCurrentA
                    duration: delegateRoot.sputterDuration
                    chamberRecovery: delegateRoot.sputterChamberRecovery
                }
            }

            Component {
                id: gisContentComponent
                GISDeposition {
                    instanceId: delegateRoot.instanceId
                    positionId: delegateRoot.positionId
                    duration: delegateRoot.gisDuration
                    chamberRecovery: delegateRoot.gisChamberRecovery
                }
            }

            // Workflow → UI routing for this row. cpWorkflow emits
            // activityStatusChanged with the row's instance_id; we
            // filter on it because the Cryo page can host multiple
            // instances of the same activity_id. validationFailed
            // is also routed by instance_id and lands the row in
            // the "exception" state with the reason as the
            // status-icon tooltip message.
            Connections {
                target: appController.cpWorkflow
                ignoreUnknownSignals: true

                function onWorkflowStarted() {
                    // Only wipe the slate for activities that are about to
                    // run (switched on). Completed activities that auto-
                    // disabled keep their "complete" badge until the user
                    // re-arms them by toggling the switch back on.
                    if (activityContainer.switchChecked) {
                        activityContainer.activityState = "idle"
                        activityContainer.statusMessage = ""
                    }
                }

                function onActivityStatusChanged(activity_id, instance_id, status) {
                    if (instance_id !== delegateRoot.instanceId) return
                    activityContainer.activityState = status

                    // Compose the status-icon tooltip via the runner's
                    // Q_INVOKABLE accessors. ``onValidationFailed``
                    // below handles a different path: pre-run
                    // validation failures emit no
                    // ``activityStatusChanged`` and instead route here
                    // through their own signal, setting the validation
                    // reason as the tooltip. The two paths are
                    // disjoint — an activity is either rejected at
                    // validation time (no run, no duration) or runs
                    // and lands here.
                    if (status === "complete") {
                        // Auto-disable on success so a completed activity
                        // doesn't re-run on the next Start; the "complete"
                        // badge stays until the user re-arms it (onToggled).
                        // Setting switchChecked here does not emit the
                        // container's toggled signal, so we push the
                        // enabled-state change to Python explicitly.
                        activityContainer.switchChecked = false
                        appController.cpWorkflow.set_activity_enabled(
                            delegateRoot.instanceId, false,
                        )
                        const dur = appController.cpWorkflow.activityDuration(
                            activity_id, instance_id,
                        )
                        activityContainer.statusMessage = "Total duration: "
                            + appController.cpWorkflow.formatDuration(dur)
                    } else if (status === "exception") {
                        const msg = appController.cpWorkflow.lastStatusMessage(
                            activity_id, instance_id,
                        )
                        activityContainer.statusMessage = (msg !== "")
                            ? msg
                            : "Failed — see log for details"
                    }
                    // status === "stop": no icon, no tooltip.
                }

                function onValidationFailed(instance_id, message) {
                    if (instance_id === delegateRoot.instanceId) {
                        activityContainer.activityState = "exception"
                        activityContainer.statusMessage = message
                    }
                }
            }

            width: ListView.view.width
            height: contentSlot.implicitHeight + AppConfig.activityGap

            Item {

                id: contentSlot

                // No anchors. contentSlot sits at (0, 0) inside
                // delegateRoot with width = parent.width. When drag
                // activates, only ParentChange runs — no anchor
                // bindings to tear down and rebuild.
                width: parent.width
                implicitHeight: activityContainer.implicitHeight

                opacity: dragging ? 0.6 : 1.0
                Behavior on opacity {
                    NumberAnimation { duration: 120; easing.type: Easing.OutQuad }
                }

                readonly property bool dragging:
                    activityContainer.dragHandleMouseArea.drag.active

                // Execute the deferred reorder when drag ends.
                onDraggingChanged: {
                    if (dragging) return

                    if (root.dropTargetDelegate !== null) {
                        const from = delegateRoot.DelegateModel.itemsIndex
                        const target = root.dropTargetDelegate
                        let insertAt = target.DelegateModel.itemsIndex
                        if (root.dropDirection === "below")
                            insertAt += 1

                        const to = insertAt - (from < insertAt ? 1 : 0)
                        if (to !== from) {
                            // Drive the controller, NOT the
                            // DelegateModel directly — the model is
                            // the source of truth and emits the
                            // signals that the DelegateModel watches.
                            appController.cryoActivities.move(from, to)
                        }
                    }

                    root.dropTargetDelegate = null
                    root.dropDirection = ""
                }

                Drag.active: contentSlot.dragging
                Drag.source: delegateRoot
                Drag.hotSpot.x: width / 2
                Drag.hotSpot.y: height / 2

                states: State {
                    when: contentSlot.dragging
                    ParentChange {
                        target: contentSlot
                        parent: activitiesListView
                    }
                }

                ActivityContainer {

                    id: activityContainer
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: root.activityWidth
                    title: delegateRoot.title
                    titleToolTip: delegateRoot.titleToolTip
                    parameterSummary: contentLoader.item
                                      ? contentLoader.item.parameterSummary
                                      : ""

                    // Stack title above parameter summary. Cryo
                    // activities have parameters dense enough
                    // (sputter: grid + species + current + duration
                    // + recovery; gis: position + duration +
                    // recovery) that the inline layout elides
                    // aggressively at common page widths. The
                    // Stage Positions header and Home Stage footer
                    // keep the inline layout (default) since they
                    // have no parameter summary or only a short one.
                    stackHeader: true

                    // Drag handle: always present (so the column is
                    // reserved and the body width is constant), but
                    // inert during a workflow run. The iterative-fetch
                    // runner walks the model in current order each
                    // iteration, so reordering mid-run *would*
                    // technically work — but it's surprising UX, so
                    // we lock it. Hiding the handle would shift the
                    // body width when a run starts; dimming it
                    // preserves layout.
                    dragHandleVisible: true
                    dragHandleEnabled: !appController.cpWorkflow
                                       || !appController.cpWorkflow.isRunning

                    // Switch enable rule mirrors the RT page — see the
                    // header comment block above for the full rationale.
                    // `activityState` referenced unqualified to dodge
                    // QML's self-reference binding-loop warning.
                    switchEnabled: !appController.cpWorkflow
                                   || !appController.cpWorkflow.isRunning
                                   || activityState === "idle"

                    onToggled: function(enabled) {
                                            if (appController.cpWorkflow) {
                                                appController.cpWorkflow.set_activity_enabled(
                                                    delegateRoot.instanceId, enabled
                                                )
                                            }
                                            // Re-arming a completed activity clears its
                                            // "complete" badge so it reads as idle/ready again.
                                            if (enabled) {
                                                activityContainer.activityState = "idle"
                                                activityContainer.statusMessage = ""
                                            }
                                            // Sputter Coat: when toggled on, ask the loaded
                                            // SputterCoat instance to pre-fill its ion
                                            // species (and matching beam current) from the
                                            // microscope's current plasma gas. UX shortcut
                                            // for operators running with a non-default gas
                                            // (e.g. Oxygen) who would otherwise have to
                                            // manually update every fresh sputter activity
                                            // from the Xenon catalog default.
                                            //
                                            // See SputterCoat.qml's
                                            // syncIonSpeciesToMicroscope() for the contract
                                            // and CPWorkflow.current_microscope_ion_species_index()
                                            // for the failure-mode behaviour (silent no-op
                                            // on disconnect / unmappable / read failure).
                                            //
                                            // The `sputter.syncIonSpeciesToMicroscope` guard
                                            // protects against the rare case where the
                                            // Loader hasn't materialised its item yet, and
                                            // is also why this works only on sputter rows:
                                            // GIS Deposition simply doesn't have the
                                            // function so the guard fails closed.
                                            if (enabled && delegateRoot.activityType === "sputter_coat") {
                                                const sputter = contentLoader.item
                                                if (sputter && sputter.syncIonSpeciesToMicroscope) {
                                                    sputter.syncIonSpeciesToMicroscope()
                                                }
                                            }
                                        }

                    Loader {
                        id: contentLoader
                        Layout.fillWidth: true
                        sourceComponent: {
                            switch (delegateRoot.activityType) {
                                case "sputter_coat":   return sputterContentComponent
                                case "gis_deposition": return gisContentComponent
                                default:               return null
                            }
                        }
                    }

                    headerExtraData: [
                        Button {
                            id: deleteButton
                            flat: true
                            icon.source: AppConfig.iconClose
                            icon.width: 14
                            icon.height: 14
                            icon.color: enabled && hovered
                                        ? AppConfig.universalForeground
                                        : AppConfig.activityRemoveButtonColor
                            opacity: enabled ? 1.0 : 0.4
                            Behavior on opacity {
                                NumberAnimation { duration: 120 }
                            }
                            implicitWidth: 24
                            implicitHeight: 24
                            padding: 0
                            enabled: activityContainer.activityState !== "running"
                            ToolTip.text: Strings.removeActivityButtonTooltip
                            ToolTip.delay: AppConfig.toolTipDelayMs
                            ToolTip.timeout: AppConfig.toolTipTimeoutMs
                            ToolTip.visible: hovered && enabled
                            onClicked: {
                                // Clear the workflow's enabled-set
                                // entry first so a removed-and-readded
                                // activity doesn't inherit a stale
                                // "enabled" flag (instance ids are
                                // fresh on each add but defense in
                                // depth is cheap here).
                                if (appController.cpWorkflow) {
                                    appController.cpWorkflow.forget_activity(
                                        delegateRoot.instanceId
                                    )
                                }
                                appController.cryoActivities.remove_at(
                                    delegateRoot.index
                                )
                            }
                        }
                    ]
                }

                Binding {
                    target: activityContainer.dragHandleMouseArea.drag
                    property: "target"
                    value: contentSlot
                }
            }

            // DropArea (unchanged from previous version — tracks where
            // a drop would land if released now).
            DropArea {
                id: delegateDropArea
                anchors.fill: parent

                function updateDropTarget(drag) {
                    if (drag.source === delegateRoot) {
                        root.dropTargetDelegate = null
                        root.dropDirection = ""
                    } else {
                        root.dropTargetDelegate = delegateRoot
                        root.dropDirection = drag.y < height / 2 ? "above" : "below"
                    }
                }

                onEntered: (drag) => updateDropTarget(drag)
                onPositionChanged: (drag) => updateDropTarget(drag)
            }
        }
    }

    // ------------------------------------------------------------------
    // Page layout
    // ------------------------------------------------------------------
    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {
            text: "Cryo Temperature Preparation"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true
        }

        Label {
            visible: !appController.settings.compactMode
            text: "Activities intended to be used when the stage is at cryo "
                + "temperatures. Activities begin from the top of the list. "
                + "Drag the handle on the left of an activity to reorder it."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap
        }

        Item {
            Layout.preferredHeight: appController.settings.compactMode
                                    ? 0 : AppConfig.pageHeadingSpacerHeight
        }

        // --- Scroll area ---
        ListView {

            id: activitiesListView
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.alignment: Qt.AlignHCenter
            clip: true
            spacing: 0

            header: stagePositionsHeader
            footer: homeStageFooter

            // The DelegateModel wraps our QAbstractListModel so we get
            // drag-and-drop visual ordering. The underlying source of
            // truth is appController.cryoActivities.model.
            model: DelegateModel {
                id: visualModel
                model: appController.cryoActivities.model
                delegate: activityDelegate
            }

            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            displaced: Transition {
                NumberAnimation {
                    properties: "x,y"
                    duration: 160
                    easing.type: Easing.OutQuad
                }
            }

            add: Transition {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 200 }
            }

            remove: Transition {
                NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 150 }
            }

            removeDisplaced: Transition {
                NumberAnimation { properties: "x,y"; duration: 160; easing.type: Easing.OutQuad }
            }
        }

        // --- Action buttons ---
        RowLayout {

            Layout.fillWidth: true
            spacing: AppConfig.buttonRowSpacing

            RoundButton {

                id: saveButton
                text: "Save"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding
                ToolTip.text: Strings.saveButtonTooltip
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                ToolTip.visible: hovered

                // Templates need positions wired (microscope-dependent); also
                // gated against mid-run modification, mirroring the Reset
                // Parameters button.
                enabled: appController.stagePositions !== null
                         && appController.cpWorkflow
                         && !appController.cpWorkflow.isRunning

                // Open the save-options dialog first (a native FileDialog
                // can't host the include-positions controls); it opens the
                // file picker on accept. Counts are refreshed here so the
                // radio labels reflect the current activity list / library.
                onClicked: {
                    saveOptionsDialog.referencedCount =
                        appController.cryoActivities.referenced_position_count()
                    saveOptionsDialog.totalCount =
                        appController.stagePositions
                            ? appController.stagePositions.model.count : 0
                    saveOptionsDialog.open()
                }

            }

            RoundButton {

                id: loadButton
                text: "Load"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding
                ToolTip.text: Strings.loadButtonTooltip
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                ToolTip.visible: hovered

                enabled: appController.stagePositions !== null
                         && appController.cpWorkflow
                         && !appController.cpWorkflow.isRunning

                onClicked: loadTemplateDialog.open()

            }

            Item { Layout.fillWidth: true }

            RoundButton {
                id: addSputterButton
                text: "Add Sputter"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding
                ToolTip.text: Strings.addSputterCoatActivityTooltip
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                ToolTip.visible: hovered

                onClicked: appController.cryoActivities.add_sputter_coat()
            }

            RoundButton {
                id: addGISButton
                text: "Add GIS"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding
                ToolTip.text: Strings.addGISDepositionActivityTooltip
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                ToolTip.visible: hovered

                onClicked: appController.cryoActivities.add_gis_deposition()
            }

            Item { Layout.fillWidth: true }

            RoundButton {
                id: startButton
                text: "Start"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding

                // ToolTip.text: Strings.startButtonTooltip
                // ToolTip.visible: hovered
                // ToolTip.delay: AppConfig.toolTipDelayMs
                // ToolTip.timeout: AppConfig.toolTipTimeoutMs

                enabled: appController.isConnected
                         && appController.cpWorkflow
                         && appController.cpWorkflow.canStart

                onClicked: {
                    if (appController.cpWorkflow) {
                        appController.cpWorkflow.start()
                    }
                }
            }

            RoundButton {
                id: stopButton
                text: "Stop"
                radius: AppConfig.buttonRadius
                padding: AppConfig.buttonPadding

                ToolTip.text: Strings.stopButtonTooltip
                ToolTip.visible: hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                enabled: appController.cpWorkflow
                         && appController.cpWorkflow.isRunning

                onClicked: {
                    if (appController.cpWorkflow) {
                        appController.cpWorkflow.stop()
                    }
                }
            }
        }
    }

    // =====================================================================
    // Templates feature — file dialogs, summary / error dialogs, signal wiring
    // =====================================================================

    // Save options (include positions + scope) — shown before the file
    // picker, which opens on accept.
    TemplateSaveOptionsDialog {
        id: saveOptionsDialog
        onAccepted: saveTemplateDialog.open()
    }

    FileDialog {
        id: saveTemplateDialog
        fileMode: FileDialog.SaveFile
        title: "Save Template"
        nameFilters: ["JSON files (*.json)"]
        defaultSuffix: "json"
        currentFolder: appController.cryoActivities.templatesDirUrl
        onAccepted: appController.cryoActivities.save_template(
                        selectedFile,
                        saveOptionsDialog.includePositions,
                        saveOptionsDialog.scope)
    }

    FileDialog {
        id: loadTemplateDialog
        fileMode: FileDialog.OpenFile
        title: "Load Template"
        nameFilters: ["JSON files (*.json)"]
        currentFolder: appController.cryoActivities.templatesDirUrl
        // Peek the file for bundled positions: if present, ask whether to
        // import them; otherwise load activities only, straight away.
        onAccepted: {
            var n = appController.cryoActivities
                        .template_position_count(selectedFile)
            if (n > 0) {
                loadOptionsDialog.fileUrl = selectedFile
                loadOptionsDialog.positionCount = n
                loadOptionsDialog.open()
            } else {
                appController.cryoActivities.load_template(selectedFile, false)
            }
        }
    }

    // Load options (import bundled positions or not) — only opened when
    // the chosen template actually carries positions.
    TemplateLoadOptionsDialog {
        id: loadOptionsDialog
        onAccepted: appController.cryoActivities.load_template(
                        fileUrl, loadPositions)
    }

    // Adjusted-load summary. Clean loads route to the StatusBar in
    // main.qml; this dialog only opens when issues.length > 0.
    ConfirmDialog {
        id: templateLoadSummaryDialog
        title: Strings.templateLoadSummaryTitle
        dismissOnly: true
    }

    // Structural failure on load — file not readable, not JSON,
    // schema-version mismatch, missing activities field, etc.
    ConfirmDialog {
        id: templateLoadErrorDialog
        title: Strings.templateLoadErrorTitle
        dismissOnly: true
    }

    // File I/O failure on save — disk full, permissions, etc.
    ConfirmDialog {
        id: templateSaveErrorDialog
        title: Strings.templateSaveErrorTitle
        dismissOnly: true
    }

    Connections {
        target: appController.cryoActivities

        function onTemplateLoadSucceeded(templateName, issues) {
            // Clean-load message lives in main.qml's StatusBar.
            // Here we handle only the adjusted-load case.
            if (issues.length === 0) {
                return
            }
            const bullets = issues.map(s => "• " + s).join("\n")
            const word = issues.length === 1 ? "adjustment" : "adjustments"
            const intro = "Template '" + templateName +
                          "' loaded with " + issues.length + " " + word +
                          ":\n\n"
            templateLoadSummaryDialog.message = intro + bullets
            templateLoadSummaryDialog.open()
        }

        function onTemplateLoadFailed(templateName, errorMessage) {
            templateLoadErrorDialog.message = errorMessage
            templateLoadErrorDialog.open()
        }

        function onTemplateSaveFailed(errorMessage) {
            templateSaveErrorDialog.message = errorMessage
            templateSaveErrorDialog.open()
        }
    }

}
