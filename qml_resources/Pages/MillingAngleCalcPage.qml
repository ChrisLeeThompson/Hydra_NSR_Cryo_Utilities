

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"
import "../Js"

// Milling angle calculator Page
//
// This page includes an interactive milling angle calculator.
// There are two interactive figures. One shows the 35 degree AutoGrid
// shuttle as it relates to the SEM, FIB, and GIS angles and positions.
// The other figure shows a cartoon of a sample where chalk lines can be
// drawn to assist with visualizing milling angles as they relate to the
// SEM, FIB, and GIS positions.
//
// Layout: the heading, description, and calculator are STATIC (the
// standard page header pattern, as on LiftoutCalcPage); only the two
// figures live inside a ScrollView, which takes the remaining vertical
// space. The calculator therefore stays visible while either figure is
// scrolled into view - the calculator and both figures are live-bound
// to the same canonical stage state. The ScrollView is clipped so the
// figures cannot paint over the static calculator while scrolling.

Item {

    id: root

    ColumnLayout {

        id: mainColumnLayout
        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Milling Angle Calculator"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            visible: !UiState.compact
            text: "This milling angle calculator is designed to assist with visualizing milling angles and "
                  + "stage tilt angles for a 35 degrees pre-tilted shuttle. The diagrams are intended to be illustrative rather than conveying precise dimensions. For example, "
                  + "the GIS position shown does not consider the angle between the GIS and the FIB along the z axis "
                  + "(where the z axis is pointing out of the board)."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap
            textFormat: Text.StyledText

        }

        Item {
            Layout.preferredHeight: UiState.compact
                                    ? 0 : AppConfig.pageHeadingSpacerHeight
        }

        ActivityContainer {

            id: millingAngleCalculatorContainer
            title: ""
            Layout.fillWidth: true
            Layout.maximumWidth: AppConfig.liftoutCalculatorContainerWidth
            Layout.alignment: Qt.AlignHCenter
            collapsible: false
            statusIconVisible: false
            switchVisible: false

            MillingAngleCalculator { id: millingAngleCalculator }

        }

        // The two figures stack taller than the remaining viewport, so
        // this region scrolls vertically. contentWidth is pinned to the
        // viewport width to disable horizontal scrolling. Plain wheel
        // events over the figures are consumed by their tilt-adjust
        // WheelHandlers, so this region only scrolls when the cursor is
        // outside the figures.
        ScrollView {

            id: figureScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {

                id: figureColumnLayout
                width: figureScroll.availableWidth
                spacing: AppConfig.pageSectionSpacing

                ActivityContainer {

                    id: shuttleDiagramContainer
                    title: ""
                    color: AppConfig.universalBackground
                    Layout.fillWidth: true
                    // Width follows the diagram's implicit size (which is derived
                    // from the gauge geometry in AppConfig) plus container chrome,
                    // so the figure and its container can never disagree.
                    Layout.maximumWidth: shuttleDiagram.implicitWidth
                                         + AppConfig.activityContainerPadding * 2
                                         + 24
                    Layout.alignment: Qt.AlignHCenter
                    collapsible: false
                    statusIconVisible: false
                    switchVisible: false

                    ShuttleDiagram {

                        id: shuttleDiagram
                        Layout.alignment: Qt.AlignHCenter

                        // The calculator's canonical state drives the figure.
                        stageTiltAngle: millingAngleCalculator.stageTiltAngle
                        rotationRegime: millingAngleCalculator.rotationRegime

                        // Clicking a tilt gauge label animates the canonical
                        // stage tilt; the figure (and both SpinBoxes) sweep
                        // along through the bindings.
                        onStageTiltClicked: function(tiltValue) {
                            millingAngleCalculator.animateStageTiltTo(tiltValue)
                        }

                        // Wheel steps apply directly (no animation) so the
                        // figure tracks the wheel without lag.
                        onStageTiltAdjusted: function(tiltValue) {
                            // Cancel any in-flight click animation so the
                            // wheel value isn't clobbered mid-sweep.
                            millingAngleCalculator.setStageTiltDirect(tiltValue)
                        }

                        // Clicking a beam label applies its stage preset
                        // (regime + tilt) from the geometry library.
                        onBeamClicked: function(beamName) {
                            var preset = MillingAngleCalculations.beamPreset(beamName)
                            millingAngleCalculator.applyStagePreset(
                                preset.rotationRegime, preset.stageTiltDeg)
                        }

                    }

                }

                ActivityContainer {

                    id: sampleDiagramContainer
                    title: ""
                    color: AppConfig.universalBackground
                    Layout.fillWidth: true
                    // Match the shuttle container's width (same formula, bound
                    // to the shuttle figure) so the two figure containers align;
                    // the sample figure is narrower and centers inside it.
                    Layout.maximumWidth: shuttleDiagram.implicitWidth
                                         + AppConfig.activityContainerPadding * 2
                                         + 24
                    Layout.alignment: Qt.AlignHCenter
                    collapsible: false
                    statusIconVisible: false
                    switchVisible: false

                    SampleDiagram {

                        id: sampleDiagram
                        Layout.alignment: Qt.AlignHCenter

                        // Same canonical state as the shuttle figure.
                        stageTiltAngle: millingAngleCalculator.stageTiltAngle
                        rotationRegime: millingAngleCalculator.rotationRegime

                        // Preview the next cut while hovering the Add button.
                        previewVisible: addChalkLineButton.hovered

                        // Labels cycle current-regime positions; chips can
                        // also target the other regime, so route regime + tilt
                        // through the shared preset path (regime switch plus
                        // animated tilt).
                        onStageOrientationRequested: function(rotationRegimeTarget,
                                                              tiltValue) {
                            millingAngleCalculator.applyStagePreset(
                                rotationRegimeTarget, tiltValue)
                        }

                        // Wheel steps apply directly (no animation) so the
                        // figure tracks the wheel without lag.
                        onStageTiltAdjusted: function(tiltValue) {
                            // Cancel any in-flight click animation so the
                            // wheel value isn't clobbered mid-sweep.
                            millingAngleCalculator.setStageTiltDirect(tiltValue)
                        }

                    }

                    RowLayout {

                        Layout.alignment: Qt.AlignHCenter
                        spacing: 12

                        RoundButton {
                            id: addChalkLineButton
                            radius: AppConfig.buttonRadius
                            text: "Add Chalk Line"
                            onClicked: sampleDiagram.addChalkLine()
                        }

                        RoundButton {
                            radius: AppConfig.buttonRadius
                            text: "Remove Last"
                            enabled: sampleDiagram.chalkLineCount > 0
                            onClicked: sampleDiagram.removeLastChalkLine()
                        }

                        RoundButton {
                            radius: AppConfig.buttonRadius
                            text: "Clear All"
                            enabled: sampleDiagram.chalkLineCount > 0
                            onClicked: sampleDiagram.clearAllChalkLines()
                        }

                    }

                }

            }

        }

    }

}

