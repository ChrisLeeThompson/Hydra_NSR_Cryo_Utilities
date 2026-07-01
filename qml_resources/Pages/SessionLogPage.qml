import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// Session Log Page.
//
// A read-mostly log of past workflow sessions, with one editable
// field per session (notes). Backed by ``appController.sessionLog``
// — its model is bound directly to the ListView below.
//
// Two-tier flat ListView (the design ratified during the session-log
// grill): rows are interleaved session headers and their activity
// children, in chronological file order. The delegate switches its
// rendering based on the ``kind`` role rather than using a tree
// model — keeps the page cheap to build and ListView's scroll
// behavior straightforward.
//
// Auto-scroll-to-end on initial load and on each new row (the
// live-tail experience: a running workflow's activities appear at
// the bottom as they finish, where the user's eye naturally tracks).

Item {

    id: root

    // Page-root alias to the session log controller. Delegate
    // bindings inside the ListView reach this via the stable ``root``
    // id rather than via the bare ``appController`` context property,
    // which doesn't always resolve cleanly inside dynamically-
    // instantiated delegate contexts at first-bind time. The id-
    // anchored reference is the standard QML idiom for "give the
    // delegate a way to reach the page" and is more robust than
    // re-referencing the context property in every binding.
    readonly property var sessionLog: appController.sessionLog

    // No cross-page disable — the Session Log page is read-only
    // (aside from per-session notes editing, which doesn't touch
    // hardware). Stays available regardless of which other page's
    // workflow is running. Notes can even be edited mid-workflow
    // on a previous session.

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Session Log"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            text: "RT Prep and Cryo Prep activity information is saved to a session log file and presented here."
                + " Notes for each session can be added/edited. The session log file can be deleted from the Settings page."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap

        }

        // --- File path + open-folder affordance --------------------------

        RowLayout {

            Layout.fillWidth: true
            spacing: 8

            Label {

                text: "Saved to:"
                font.pixelSize: AppConfig.pageBodyFontSize
                color: AppConfig.universalForeground

            }

            Label {

                Layout.fillWidth: true
                text: root.sessionLog.displayPath
                font.pixelSize: AppConfig.pageBodyFontSize
                color: AppConfig.universalForeground
                // No elide — displayPath is already in its
                // truncated form (``"...{sep}<project_root>{sep}…"``).
                // The full absolute path is on the tooltip for
                // anyone who needs it (e.g. to ``cd`` to the
                // directory).

                ToolTip.text: root.sessionLog.filePath
                ToolTip.visible: hoverHandler.hovered
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs

                HoverHandler { id: hoverHandler }

            }

            RoundButton {

                id: openSessionLogsButton
                Layout.alignment: Qt.AlignRight
                text: "Open Folder"
                radius: AppConfig.buttonRadius

                ToolTip.text: "Open the containing folder in the system file manager"
                ToolTip.delay: AppConfig.toolTipDelayMs
                ToolTip.timeout: AppConfig.toolTipTimeoutMs
                ToolTip.visible: hovered

                onClicked: Qt.openUrlExternally(root.sessionLog.directoryUrl)

            }

        }

        Item { Layout.preferredHeight: AppConfig.pageHeadingSpacerHeight }

        // --- The log itself ----------------------------------------------

        // Stack: empty-state Label and the ListView occupy the same
        // space; ``visible`` toggles based on whether there's
        // anything to show. This way the empty state is centered
        // properly without needing a separate layout branch.
        Item {

            Layout.fillWidth: true
            Layout.fillHeight: true

            Label {
                anchors.centerIn: parent
                visible: root.sessionLog.model.count === 0
                text: "No sessions logged."
                horizontalAlignment: Text.AlignHCenter
                font.pixelSize: AppConfig.pageBodyFontSize
                color: AppConfig.universalForeground
            }

            ListView {

                id: sessionListView
                anchors.fill: parent
                visible: root.sessionLog.model.count > 0
                clip: true
                spacing: 4
                model: root.sessionLog.model

                // Cache the controller on the ListView itself so the
                // delegate can reach it via ``sessionListView.sl``.
                // Reaching ``root.sessionLog`` from the delegate's
                // scope is unreliable at first-bind time in some QML
                // configurations (the property alias evaluates to
                // null inside the dynamically-instantiated delegate
                // context even though it resolves fine at page
                // root); a sibling-ListView property is the textbook
                // pattern and works robustly because the ListView's
                // own id is in scope wherever the delegate is.
                property var sl: root.sessionLog

                ScrollBar.vertical: ScrollBar {
                    id: verticalScrollBar
                    active: true
                }

                // Auto-scroll-to-end behaviour. The locked design
                // choice is chronological order (oldest at top,
                // newest at the bottom — live-tail style). On
                // initial load, position at the end so the user
                // lands on "today" rather than scrolling through
                // months of history. On each new row, same
                // positioning so a running workflow's activities
                // stay visible.
                Component.onCompleted: positionViewAtEnd()

                onCountChanged: {
                    // Defer one event-loop tick so the new row's
                    // height is laid out before we position. Without
                    // this, positionViewAtEnd lands one row short on
                    // some Qt versions.
                    Qt.callLater(positionViewAtEnd)
                }

                delegate: Item {

                    id: delegateRoot

                    // Roles exposed by SessionLogModel. One row IS one
                    // session — activities are nested as a list under
                    // ``activities``. No ``kind`` discriminator, no
                    // activity-only roles at this level; the activity
                    // dicts inside ``activities`` carry their own
                    // camelCase fields (``activityId``, ``durationS``,
                    // ``result``, ``lastStatus``, ``params``) that the
                    // Repeater's modelData exposes inside the card.
                    required property string sessionId
                    required property var workflowId
                    required property var startedAt
                    required property var notes
                    required property var endedAt
                    required property var allComplete
                    required property var totalDurationS
                    required property bool isInterrupted
                    required property bool isRunning
                    required property var activities

                    // Single source of truth for the badge. Both the
                    // badge's ``text`` and ``color`` derive from this
                    // one switch, so the two can't drift and the
                    // ``activities.some(...)`` exception scan runs
                    // once per row update instead of twice.
                    //
                    // Precedence: ``isRunning`` is checked BEFORE
                    // ``isInterrupted``. A live session also has
                    // ``ended_at === null`` (so ``isInterrupted`` is
                    // true at the disk level); intercepting the
                    // running case first lets ``isInterrupted`` keep
                    // its honest meaning ("no session_end record")
                    // with no Python-side redefinition. ``isRunning``
                    // itself is controller-derived runtime state —
                    // see ``SessionLog._row_for``.
                    //
                    // The "—" sentinel is intentionally a visibly-
                    // wrong placeholder, not a plausible word: the
                    // preceding branches are exhaustive (``endedAt``
                    // and ``allComplete`` co-vary by construction),
                    // so if it ever shows on screen it should read
                    // as "the state machine has a hole," not
                    // masquerade as a real status.
                    readonly property string sessionState: {
                        if (isRunning) return "running"
                        if (isInterrupted) return "interrupted"
                        if (allComplete === false
                                && activities.some(a => a.result === "exception"))
                            return "exception"
                        if (allComplete === true) return "complete"
                        if (allComplete === false) return "stopped"
                        return "unknown"
                    }

                    width: ListView.view.width
                    implicitHeight: sessionCard.implicitHeight + cardGap

                    // Small vertical gap between cards. Lives on the
                    // delegate wrapper, not on the card itself, so
                    // the gap is included in the row height but the
                    // card's anchors land cleanly within it.
                    readonly property int cardGap: 8

                    Rectangle {

                        id: sessionCard
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.leftMargin: AppConfig.activityContainerSideMargin
                        // Reserve extra space on the right for the
                        // vertical scrollbar so the card doesn't butt
                        // up against it visually. Binding to
                        // ``verticalScrollBar.width`` (rather than a
                        // hardcoded value) keeps the spacing accurate
                        // if the theme changes the scrollbar's
                        // metrics. The card's left margin uses just
                        // ``sideMargin`` so the cards remain visually
                        // balanced against the page edge on the left
                        // and the scrollbar on the right.
                        anchors.rightMargin: AppConfig.activityContainerSideMargin
                                             + verticalScrollBar.width
                        color: AppConfig.activityContainerBackground
                        radius: AppConfig.activityContainerRadius
                        border.width: AppConfig.activityContainerBorderWidth
                        // Idle border throughout — the box itself is
                        // visually neutral. Outcome state still reads
                        // at a glance via the colored badge in the
                        // top-right of the header row.
                        border.color: AppConfig.activityContainerIdleBorder
                        implicitHeight: cardContent.implicitHeight
                                        + 2 * AppConfig.activityContainerPadding

                        // Click-outside-to-defocus. A MouseArea behind
                        // the content (``z: -1``) captures clicks on
                        // the card's background — areas not occupied
                        // by an interactive child like the notes
                        // TextField — and forces focus onto the card
                        // itself, removing focus from the TextField.
                        // The TextField sits on top and intercepts
                        // its own clicks first, so clicking *on* the
                        // field still gives it focus normally. Used
                        // together with ``onAccepted`` on the
                        // TextField (Enter/Return), this gives the
                        // user two ways to commit a note without an
                        // explicit "save" button — and removes the
                        // surprise of "I clicked away but my cursor
                        // is still in the field."
                        MouseArea {
                            anchors.fill: parent
                            z: -1
                            onClicked: sessionCard.forceActiveFocus()
                        }

                        ColumnLayout {

                            id: cardContent
                            anchors.fill: parent
                            anchors.margins: AppConfig.activityContainerPadding
                            spacing: AppConfig.activityFormRowSpacing

                            // --- Header row: workflow + timestamp + status badge -------

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: AppConfig.activityIconTitleGap

                                Label {
                                    text: sessionListView.sl
                                            .formatWorkflowName(delegateRoot.workflowId)
                                    font.pixelSize: AppConfig.activityTitleFontSize
                                    font.bold: true
                                }

                                Label {
                                    text: sessionListView.sl
                                            .formatTimestamp(delegateRoot.startedAt)
                                    font.pixelSize: AppConfig.activityTitleFontSize
                                    color: AppConfig.universalForeground
                                    Layout.leftMargin: 12
                                }

                                Item { Layout.fillWidth: true }

                                // Status badge — colored text label.
                                // This is the only place state shows
                                // (the box border is always idle), so
                                // it carries the full visual weight
                                // of outcome communication. Both
                                // switches key off the delegate's
                                // ``sessionState`` property — the one
                                // place the state logic lives.
                                Label {
                                    text: {
                                        switch (delegateRoot.sessionState) {
                                        case "running":     return "Running"
                                        case "interrupted": return "Interrupted"
                                        case "exception":   return "Exception"
                                        case "complete":    return "Complete"
                                        case "stopped":     return "Stopped"
                                        default:            return "—"
                                        }
                                    }
                                    color: {
                                        switch (delegateRoot.sessionState) {
                                        case "running":     return AppConfig.activityRunningColor
                                        case "interrupted": return AppConfig.activityExceptionColor
                                        case "exception":   return AppConfig.activityExceptionColor
                                        case "complete":    return AppConfig.activityCompleteColor
                                        case "stopped":     return AppConfig.textDisabledColor
                                        default:            return AppConfig.textDisabledColor
                                        }
                                    }
                                    font.pixelSize: AppConfig.pageBodyFontSize
                                    font.bold: true
                                }
                            }

                            // --- Notes row: editable text field ----------------

                            RowLayout {

                                Layout.fillWidth: true
                                spacing: AppConfig.activityIconTitleGap

                                TextField {
                                    id: notesField
                                    Layout.fillWidth: true
                                    placeholderText: "Note for this session…"
                                    placeholderTextColor: AppConfig.placeholderTextColor
                                    font.pixelSize: AppConfig.pageBodyFontSize

                                    // Focus-gated binding: the field's text
                                    // tracks the model's notes value only
                                    // when the user is NOT actively editing.
                                    // Without this, an activity arriving
                                    // mid-workflow triggers row-level
                                    // dataChanged, which would re-evaluate
                                    // a naive ``text: notes`` binding and
                                    // clobber the user's in-progress typing.
                                    // With the gate, the binding sits
                                    // dormant while focused; once the user
                                    // unfocuses (or presses Enter) the
                                    // editingFinished handler commits, the
                                    // model updates, and the binding
                                    // re-evaluates to a value matching what
                                    // they just typed — no visible jump.
                                    Binding on text {
                                        value: delegateRoot.notes ?? ""
                                        when: !notesField.activeFocus
                                    }

                                    // Enter/Return commits the note and
                                    // visually deselects the field — gives
                                    // the user clear feedback that input
                                    // was accepted (cursor disappears,
                                    // selection clears). The actual model
                                    // commit happens via editingFinished,
                                    // which fires on both Enter and on
                                    // focus loss; forcing focus away from
                                    // the field triggers focus-loss, which
                                    // in turn triggers editingFinished —
                                    // a single commit path either way.
                                    onAccepted: sessionCard.forceActiveFocus()

                                    onEditingFinished: {
                                        if (text !== (delegateRoot.notes ?? "")) {
                                            sessionListView.sl.editNote(
                                                delegateRoot.sessionId, text
                                            )
                                        }
                                    }
                                }
                            }

                            // --- Activities: nested via Repeater ------------------------

                            // A subtle separator line above the activities
                            // section — visually delineates the editable
                            // metadata above (header, notes) from the
                            // factual activity record below. Same treatment
                            // the codebase uses for activity-container
                            // separators (``activitySeparatorColor``), but
                            // we use the idle-border color instead for a
                            // quieter look — the section grouping inside
                            // a card should be unobtrusive, not loud.

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                Layout.bottomMargin: 2
                                Layout.preferredHeight: 1
                                color: AppConfig.activityContainerIdleBorder
                                visible: delegateRoot.activities
                                         && delegateRoot.activities.length > 0
                            }

                            Repeater {

                                model: delegateRoot.activities

                                delegate: ColumnLayout {

                                    id: activityItem
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: 2

                                    // First line: status dot + activity name + duration.
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: AppConfig.activityIconTitleGap

                                        Rectangle {
                                            width: 10
                                            height: 10
                                            radius: 5
                                            color: {
                                                switch (activityItem.modelData.result) {
                                                    case "complete": return AppConfig.activityCompleteColor
                                                    case "exception": return AppConfig.activityExceptionColor
                                                    case "stop": return AppConfig.textDisabledColor
                                                    default: return AppConfig.textDisabledColor
                                                }
                                            }
                                        }

                                        Label {
                                            text: sessionListView.sl
                                                    .formatActivityName(activityItem.modelData.activityId)
                                            font.pixelSize: AppConfig.pageBodyFontSize
                                        }

                                        Item { Layout.fillWidth: true }

                                        Label {
                                            text: sessionListView.sl
                                                    .formatDuration(activityItem.modelData.durationS)
                                            font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                            color: AppConfig.placeholderTextColor
                                        }
                                    }

                                    // Second line: formatted params (or
                                    // lastStatus on the exception path,
                                    // shown in warning color so the
                                    // reason a step failed is right there
                                    // without needing a tooltip).
                                    Label {
                                        Layout.fillWidth: true
                                        Layout.leftMargin: 18   // align under name
                                        text: {
                                            if (activityItem.modelData.result === "exception") {
                                                return activityItem.modelData.lastStatus || ""
                                            }
                                            return sessionListView.sl.formatParameters(
                                                activityItem.modelData.activityId,
                                                activityItem.modelData.params
                                            )
                                        }
                                        color: activityItem.modelData.result === "exception"
                                               ? AppConfig.activityExceptionColor
                                               : AppConfig.activityParameterSummaryColor
                                        font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                        wrapMode: Label.WordWrap
                                        visible: text.length > 0
                                    }
                                }
                            }

                            // --- Footer: activities count + total duration --------------

                            RowLayout {
                                Layout.fillWidth: true
                                Layout.topMargin: 4
                                spacing: AppConfig.activityIconTitleGap

                                Label {
                                    text: {
                                        const n = delegateRoot.activities
                                                  ? delegateRoot.activities.length
                                                  : 0
                                        const word = n === 1 ? "activity" : "activities"
                                        return n + " " + word
                                    }
                                    font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                    color: AppConfig.activityParameterSummaryColor
                                }

                                Item { Layout.fillWidth: true }

                                Label {
                                    visible: delegateRoot.totalDurationS !== null
                                             && delegateRoot.totalDurationS !== undefined
                                    text: visible
                                          ? sessionListView.sl
                                                .formatDuration(delegateRoot.totalDurationS)
                                          : ""
                                    font.pixelSize: AppConfig.activityParameterSummaryFontSize
                                    color: AppConfig.activityParameterSummaryColor
                                }
                            }

                        }

                    }

                }

            }

        }

    }

}
