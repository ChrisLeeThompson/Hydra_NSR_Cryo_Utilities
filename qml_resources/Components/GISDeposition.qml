import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"

// GIS Deposition activity.
//
// Deposits material on the grid using the GIS at a saved stage
// position. The activity exposes:
//   • a position ComboBox sourced from the StagePositions controller
//   • a duration spin box (seconds)
//   • a chamber recovery spin box (seconds)
//
// Per-instance state model: same pattern as SputterCoat.qml. Multiple
// GIS Deposition instances can exist on the Cryo page; each is keyed
// by `instanceId` and binds two-way to its model row through the
// CryoActivitiesController.
//
// Position binding:
//
//   The position is stored as an opaque id (see SavedStagePosition.id),
//   not as an index. Rationale: positions can be reordered, renamed,
//   added, or removed — the index of a given position can shift.
//   Storing the id means a saved Cryo workflow keeps pointing at the
//   same physical position across all those operations. The QML
//   ComboBox's currentIndex is derived from the id at bind time.
//
//   When a referenced position is deleted from the Stage Positions
//   list, the saved id becomes stale. The ComboBox shows no current
//   selection (currentIndex = -1) and the workflow refuses to start
//   activities with unselected positions. This is detected at Start
//   time, not eagerly — see the comment on
//   defaults.GIS_DEPOSITION_POSITION_ID_DEFAULT.

GroupBox {

    id: root

    // --- Per-instance binding ---
    property string instanceId: ""

    // Model-row sourced parameter values.
    property string positionId: ""
    property int duration: 0
    property int chamberRecovery: 0

    Component.onCompleted: {
        if (!root.instanceId) return
        if (!root.positionsModel) return
        if (root.positionsModel.count === 0) return
        if (root.positionsModel.find_index_by_id(root.positionId) >= 0) return

        const rec = root.positionsModel.get(0)
        if (rec && rec.id) {
            appController.cryoActivities.set_gis_position_id(
                root.instanceId, rec.id
            )
        }
    }

    // --- Stage positions model ---
    readonly property var positionsModel:
        appController.stagePositions
            ? appController.stagePositions.model
            : null

    // --- Activity header summary ---
    //
    // The position name is derived from the current id by looking it
    // up in the positions model. If the id isn't found (e.g. the
    // position was deleted) the summary shows "—" so the user notices
    // something is wrong without the activity silently looking valid.
    //
    // The _modelRevision counter forces the binding to re-evaluate
    // whenever positions are added, removed, renamed, or reset.
    // Without it, the binding only depends on positionsModel and
    // positionId — neither of which change when a position is renamed
    // or when a position with a *different* id is deleted (the
    // model's row count drops but our id-based lookup wouldn't be
    // invalidated). Reading _modelRevision (a "binding witness") is a
    // common QML pattern for tying a function-call result to an
    // external event source.
    readonly property string _positionName: {
        const _ = root._modelRevision   // dependency anchor
        if (!positionsModel || !positionId) return ""
        const idx = positionsModel.find_index_by_id(positionId)
        if (idx < 0) return ""
        return positionsModel.get(idx).name
    }

    readonly property string parameterSummary:
        (_positionName || "—") + ", " + duration + "s"

    // ----------------------------------------------------------------------
    // Model-revision counter — bumped whenever the positions model
    // changes (rows added, removed, reordered, or data updated). The
    // _positionName binding reads this so it re-evaluates on each
    // change, picking up renames and deletions of the position this
    // activity references.
    // ----------------------------------------------------------------------
    property int _modelRevision: 0

    Connections {
        target: root.positionsModel
        ignoreUnknownSignals: true
        function onDataChanged()    { root._modelRevision++ }
        function onRowsInserted()   { root._modelRevision++ }
        function onRowsRemoved()    { root._modelRevision++ }
        function onModelReset()     { root._modelRevision++ }
    }

    Layout.fillWidth: true
    focusPolicy: Qt.StrongFocus
    background: Rectangle {
        anchors.fill: parent
        color: "transparent"
    }

    GridLayout {

        anchors.fill: parent
        columns: 2
        rowSpacing: AppConfig.activityFormRowSpacing
        columnSpacing: AppConfig.activityFormColumnSpacing

        // ---- Row 1: Stage position ----

        ToolTippedLabel {
            id: positionLabel
            text: "Position"
            toolTipText: Strings.positionLabelTooltip
        }

        ComboBox {
            id: positionComboBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            Layout.preferredWidth: root.width * 0.5
            model: root.positionsModel
            textRole: "name"

            // The current index is derived from positionId — but only
            // when the model and id are both available. Re-evaluates
            // whenever positionsModel.count changes (i.e. positions
            // added / removed) or when our positionId changes.
            currentIndex: {
                if (!root.positionsModel || !root.positionId) return -1
                return root.positionsModel.find_index_by_id(root.positionId)
            }

            onActivated: {
                if (!root.instanceId || !root.positionsModel) return
                const rec = root.positionsModel.get(currentIndex)
                if (rec && rec.id) {
                    appController.cryoActivities.set_gis_position_id(
                        root.instanceId, rec.id
                    )
                }
            }
        }

        // ---- Row 2: Duration ----

        ToolTippedLabel {
            id: durationLabel
            text: "Duration (s)"
            toolTipText: Strings.gisDepositionDurationLabelTooltip
        }

        CustomSpinBox {
            id: durationSpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: appController.cryoActivities.gisDurationMin
            to: appController.cryoActivities.gisDurationMax
            value: root.duration
            editable: true
            showArrows: true
            onValueModified: {
                if (root.instanceId) {
                    appController.cryoActivities.set_gis_duration(
                        root.instanceId, value
                    )
                }
            }
        }

        // ---- Row 3: Chamber recovery ----

        ToolTippedLabel {
            id: chamberRecoveryLabel
            text: "Chamber Recovery (s)"
            toolTipText: Strings.chamberRecoveryLabelTooltip
        }

        CustomSpinBox {
            id: chamberRecoverySpinBox
            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
            from: appController.cryoActivities.chamberRecoveryMin
            to: appController.cryoActivities.chamberRecoveryMax
            value: root.chamberRecovery
            editable: true
            showArrows: true
            onValueModified: {
                if (root.instanceId) {
                    appController.cryoActivities.set_gis_chamber_recovery(
                        root.instanceId, value
                    )
                }
            }
        }

    }

}
