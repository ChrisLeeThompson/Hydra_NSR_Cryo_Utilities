import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"
import "../Components"

// Lift-out calculator page
//
// This page includes the cryo lift-out calculator and a description
// block below the calculator that updates based on the lift-out type
// selected in the calculator's combo box. The description text is
// styled (HTML subset) and pulled from Strings.qml.

Item {

    id: root

    ColumnLayout {

        id: mainColumnLayout
        anchors.fill: parent
        anchors.margins: AppConfig.pageMargin
        spacing: AppConfig.pageSectionSpacing

        Label {

            text: "Lift-out Angle Calculator"
            font.pixelSize: AppConfig.pageHeadingFontSize
            font.bold: true

        }

        Label {

            visible: !appController.settings.compactMode
            text: "This lift-out calculator is designed to assist with determining stage tilt angles"
            + " for cryo lift-out procedures.<br><br>"
            + "<b>Note:</b> some of the calculated angles may result in collision between the EasyLift"
            + " needle and shuttle or AutoGrid. To avoid potential collisions (and ensure the angles will"
            + " work with your sample), it is recommended to test angles without a sample attached to the"
            + " EasyLift, and optimize the lift-out tilt angles and attach tilt angles for your applications."
            font.pixelSize: AppConfig.pageBodyFontSize
            Layout.fillWidth: true
            wrapMode: Label.WordWrap
            textFormat: Text.StyledText

        }

        Item {
            Layout.preferredHeight: appController.settings.compactMode
                                    ? 0 : AppConfig.pageHeadingSpacerHeight
        }

        ActivityContainer {

            id: liftoutCalculatorContainer
            title: ""
            Layout.fillWidth: true
            Layout.maximumWidth: AppConfig.liftoutCalculatorContainerWidth
            Layout.alignment: Qt.AlignHCenter
            collapsible: false
            statusIconVisible: false
            switchVisible: false

            LiftoutCalculator { id: liftoutCalculator }

        }

        Item { Layout.preferredHeight: AppConfig.pageHeadingSpacerHeight }

        // Scrolling, word-wrapped description block.
        //
        // The ScrollView takes the remaining vertical space (replacing
        // the old trailing fillHeight spacer) and becomes the element
        // that scrolls when the text outgrows that space. Word-wrap
        // handles the horizontal axis, so horizontal scrolling is
        // disabled outright; the vertical bar appears only on overflow.
        ScrollView {

            id: liftoutDescriptionScrollView
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            Label {

                id: liftoutDescriptionLabel
                // A Label inside a ScrollView inherits no width to wrap
                // against — left unbound it grows horizontally and a
                // horizontal scrollbar appears instead of wrapping.
                // Binding width to the ScrollView's content width is
                // what actually makes WordWrap work here (and it
                // reflows narrower if the vertical bar appears).
                width: liftoutDescriptionScrollView.availableWidth
                font.pixelSize: AppConfig.pageBodyFontSize
                wrapMode: Label.WordWrap
                textFormat: Text.StyledText
                text: {
                    switch (liftoutCalculator.selectedTypeIndex) {
                        case 0: return Strings.liftoutDescTopdownMinus70
                        case 1: return Strings.liftoutDescPlanarMinus70
                        case 2: return Strings.liftoutDescPlanar110
                        default: return ""
                    }
                }

            }

        }

    }

}
