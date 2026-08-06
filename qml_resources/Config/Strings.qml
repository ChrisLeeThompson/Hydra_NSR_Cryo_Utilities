pragma Singleton
import QtQuick
import "."

QtObject {

    // Main window. The version comes from the Python package
    // (hydra_nsr_cu.__version__) via the appVersion context property,
    // so the title always matches the actual release number.
    readonly property string mainWindowTitle:
        "Hydra NSR Cryo Utilities " + appVersion

    // Dialogs
    readonly property string stagePositionNamePlaceholderText: "Enter a position name"
    readonly property string stagePositionNameCollisionError: "Name already exists."
    readonly property string stageMoveErrorConfirmDialog: "The stage move failed. See the console log for details."

        // Generic confirm-dialog button defaults (overridable per instance)
    readonly property string dialogDefaultAcceptText: "OK"
    readonly property string dialogDefaultRejectText: "Cancel"

        // Pre-start check dialogs (main.qml)
    readonly property string preStartRefuseTitle: "Cannot Start"
    readonly property string preStartConfirmTitle: "Acknowledge Safety Check"
    readonly property string preStartConfirmAcceptText: "Acknowledge and Continue"

        // Stage positions dialogs (%1 = position name)
        // "Move To Position" keeps its capital "To" to match the "Move To"
        // button in StagePositions.qml.
    readonly property string stageMoveToTitle: "Move To Position"
    readonly property string stageMoveToMessage: "Move the stage to \"%1\"?"
    readonly property string stageUpdatePositionTitle: "Update Position"
    readonly property string stageUpdatePositionMessage: "Overwrite \"%1\" with the current stage position?"
    readonly property string stageRemovePositionTitle: "Remove Position"
    readonly property string stageRemovePositionMessage: "Remove \"%1\" from the list?"
    readonly property string stageMoveErrorTitle: "Stage Move Error"

        // Cryo template dialogs (titles only; bodies are composed in QML or supplied by Python)
    readonly property string templateLoadSummaryTitle: "Template Loaded with Adjustments"
    readonly property string templateLoadErrorTitle: "Could Not Load Template"
    readonly property string templateSaveErrorTitle: "Could Not Save Template"

        // Cryo template save / load options dialogs
    readonly property string templateSaveOptionsTitle: "Save Template"
    readonly property string templateIncludePositionsLabel: "Include stage positions"
    readonly property string templatePositionsReferencedLabel: "Only positions used by these activities"
    readonly property string templatePositionsAllLabel: "All saved positions"
    readonly property string templateSaveContinueText: "Continue…"
    readonly property string templateLoadOptionsTitle: "Load Template"
    readonly property string templateLoadPositionsLabel: "Load stage positions"
    readonly property string templateLoadPositionsHint: "New positions are added to the list. Existing positions are never overwritten."
    readonly property string templateLoadContinueText: "Load"

        // Session log (Settings.qml)
    readonly property string clearSessionLogTitle: "Clear Session Log?"
    readonly property string clearSessionLogMessage: "All logged sessions and their notes will be deleted, and the log file replaced with an empty one."

    // Tooltips
        // Activities
    readonly property string gisPurgeActivityTooltip: "Purges the Pt GIS for the specified duration."
    readonly property string homeStageActivityTooltip: "Performs a home stage procedure with rotation.\n\nThis procedure cannot be stopped once started; stopping it requires manual intervention."
    readonly property string stagePositionsActivityTooltip: "Saved stage positions used by the GIS Deposition activities."
    readonly property string gisDepositionActivityTooltip: "Deposits (condenses) material onto the grid with the GIS at the selected stage position."
    readonly property string removeActivityButtonTooltip: "Remove this activity from the list."
    readonly property string addSputterCoatActivityTooltip: "Add a sputter coat activity to the list."
    readonly property string addGISDepositionActivityTooltip: "Add a GIS deposition activity to the list."
    readonly property string chamberRecoveryLabelTooltip: "Vacuum chamber recovery time (s). Waits this long "
                                                          + "after the activity completes so chamber pressure can recover."

        // GIS purge activity
    readonly property string gisPurgeDurationLabelTooltip: "How long the GIS valve stays open to purge (s)."

        // Cryo prep page
    readonly property string saveButtonTooltip: "Save the current activity list, and optionally the saved stage positions, as a template file."
    readonly property string loadButtonTooltip: "Load activities and parameters from a template file.\n"
                                                + "This replaces the current activity list."

        // Stage positions activity
    readonly property string stageAddPositionButtonTooltip: "Add the current stage position to the list."
    readonly property string stageEditNameButtonTooltip: "Edit the name of the selected position."
    readonly property string stageMoveToButtonTooltip: "Move the stage to the selected position."
    readonly property string stageUpdatePositionButtonTooltip: "Overwrite the selected position with the current stage position."
    readonly property string stageRemovePositionButtonTooltip: "Remove the selected position from the list."

        // Sputter coat activity
    readonly property string sputterCoatActivityTooltip: "Sputter coats at the selected stage position by applying the configured pattern file with the PFIB and the µCoater GIS."
    readonly property string ionSpeciesLabelTooltip: "Ion species used for sputtering."
    readonly property string ionCurrentLabelTooltip: "Beam current used for sputtering. Available values depend on "
                                                     + "the selected ion species."
    readonly property string highVoltageLabelTooltip: "PFIB high voltage (kV) used for sputtering."
    readonly property string sputterCoatDurationLabelTooltip: "Sputter coat duration (s)."
    readonly property string bulkSputterCoatDurationButtonTooltip: "Set the duration to " + AppConfig.bulkSputterCoatDefaultDuration
                                                                   + " s (the bulk sputtering default)."
    readonly property string lamellaSputterCoatDurationButtonTooltip: "Set the duration to " + AppConfig.lamellaSputterCoatDefaultDuration
                                                                      + " s (the lamella sputtering default)."

        // Position-based activities (GIS deposition, sputter coat)
    readonly property string positionLabelTooltip: "Stage position used for the activity."
    readonly property string gisDepositionDurationLabelTooltip: "How long the GIS valve stays open (s)."

        // Stage / Scan page
    readonly property string stageRot180ButtonTooltip: "Rotate the stage 180° (relative and compucentric)."
    readonly property string setScanRot0ButtonTooltip: "Set the scan rotation of the SEM and FIB to 0°."
    readonly property string setScanRot180ButtonTooltip: "Set the scan rotation of the SEM and FIB to 180°."
    readonly property string scanRotWithStageCheckBoxTooltip: "Scan rotates the SEM and FIB 180° after the stage rotates 180°."
    readonly property string stageTiltWithRotCheckBoxTooltip: "Recommended for safe stage rotations. The stage tilts to 0° before rotation, then returns to its original tilt.\n\nIf Tilt After Rotation is enabled, the stage tilts to that angle instead."
    readonly property string tiltAfterRotationCheckBoxTooltip: "Tilts the stage to the specified angle (deg.) after rotation.\n\nWith Zero Tilt Before Rotation enabled, the stage tilts to 0° before rotating; without it, the stage rotates at its current tilt."
    readonly property string seventeenLabelTooltip: "Set Tilt After Rotation to 17°."
    readonly property string thirtyFiveLabelTooltip: "Set Tilt After Rotation to 35°."
    readonly property string stageZSliderLabelTooltip: "Drag the slider, or click its groove, to adjust the stage Z height. Step size increases the further the handle is from center."
        // Stage Z slider safety gate
    readonly property string stageZSafetyTitle: "Stage position outside safe range"
    readonly property string stageZSafetyOutOfRangeFallback: "The stage is outside the safe range. Moving Z may risk a collision — unlock only if you are sure it is safe."
    readonly property string stageZSafetyUnlockButton: "Unlock"

        // Settings
    readonly property string alwaysOnTopLabelTooltip: "Keep the UI window above all other applications."
    readonly property string compactModeLabelTooltip: "Snap the window to its compact size (subtitles hidden, headers tightened); uncheck to restore the normal size.\n\nThe checkbox follows the window, so resizing manually toggles it too. Compact mode with Always On Top is useful for placing the Stage / Scan page over a quadrant in xT Microscope Control."
    readonly property string gisGasPortNameLabelTooltip: "The name of the GIS gas port used in the GIS Purge and GIS Deposition activities."
    readonly property string sputterPatternFileLabelTooltip: "File name of the .ptf sputter pattern to apply, located in the pattern_files folder."
    readonly property string sputterCoatHfwLabelTooltip: "The PFIB horizontal field width (µm) used during sputter coating."
    readonly property string sputterCoatPortNameLabelTooltip: "The name of the GIS port used as the sputter coater (e.g. µCoater)."
    readonly property string bulkSputteringDefaultDurationLabelTooltip: "The default duration (s) for the Bulk Sputtering button in the Sputter Coat activity."
    readonly property string lamellaSputteringDefaultDurationLabelTooltip: "The default duration (s) for the Lamella Sputtering button in the Sputter Coat activity."
    readonly property string zeroTiltBeforeGisDepositionLabelTooltip: "The stage is tilted to 0° before moving to the GIS deposition position."
    readonly property string moveStageToOriginalPositionLabelTooltip: "Move the stage back to its original position after all RT Prep or Cryo Prep activities complete successfully."
    readonly property string restoreOriginalPFIBVoltageAndCurrentLabelTooltip: "Restore the original PFIB high voltage, beam current, and on/off state after all Cryo Prep activities complete successfully."
    readonly property string restoreOriginalPFIBIonSpeciesLabelTooltip: "Restore the original PFIB ion species after all Cryo Prep activities complete successfully."
    readonly property string resetRTPrepParametersLabelTooltip: "Reset all RT Prep activity parameters to their default values."
    readonly property string resetCryoPrepParametersLabelTooltip: "Reset all Cryo Prep activity parameters to their default values."
    readonly property string sessionLogSizeLabelTooltip: "Disk size of the session log file. Use the Clear Session Log button to reset it."
    readonly property string clearSessionLogLabelTooltip: "Delete all session log entries. The log file is replaced with an empty one."

        // Workflow buttons
    readonly property string startButtonTooltip: "Start the workflow.\n\n"
                                                  + "Enabled when at least one activity is switched on, "
                                                  + "the microscope is connected, and no other workflow is running."
    readonly property string stopButtonTooltip: "Stop the workflow at the next available breakpoint."

        // Lift-out Calculator
    readonly property string shuttlePretiltAngleLabelTooltip: "Shuttle pre-tilt angle (deg.)."
    readonly property string liftoutTiltAngleLabelTooltip: "Stage tilt angle (deg.) when attaching the EasyLift needle to the chunk."
    readonly property string attachTiltAngleLabelTooltip: "Stage tilt angle (deg.) when attaching the chunk to the lift-in grid (TEM grid)."
    readonly property string fibStageTiltAngleLabelTooltip: "Stage tilt angle (deg.) for FIB milling the chunk at the −70° stage rotation (lamella milling) position."
    readonly property string fibMillingAngleLabelTooltip: "FIB milling angle (deg.) with the shuttle at ~−70° stage rotation."
    readonly property string semTiltAngleLabelTooltip: "Stage tilt angle (deg.) that puts the chunk/lamella perpendicular to the SEM with the shuttle at ~−70° stage rotation. Formula: pre-tilt angle + FIB milling angle."
    readonly property string liftoutCalcResetButtonTooltip: "Reset calculator to default values."

    // Lift-out Calculator descriptions
    readonly property string liftoutDescTopdownMinus70: "<b>Top-down lift-out: −70° rotation</b><br><br>"
                                                        + "Top-down lift-out gives milling angles perpendicular to the surface of the bulk sample."
                                                        + " −70° is the standard stage rotation for milling lamellae with an AutoGrid shuttle;"
                                                        + " for this type of lift-out, the chunk is lifted out of the bulk sample at −70° stage rotation."
    readonly property string liftoutDescPlanarMinus70: "<b>Planar lift-out: −70° rotation</b><br><br>"
                                                        + "Planar lift-out gives milling angles parallel to the surface of the bulk sample."
                                                        + " −70° is the standard stage rotation for milling lamellae with an AutoGrid shuttle;"
                                                        + " for this type of lift-out, the chunk is lifted out of the bulk sample at −70° stage rotation."
    readonly property string liftoutDescPlanar110: "<b>Planar lift-out: 110° rotation</b><br><br>"
                                                        + "Planar lift-out gives milling angles parallel to the surface of the bulk sample."
                                                        + " 110° is the stage rotation 180° from the −70° position with an AutoGrid shuttle;"
                                                        + " for this type of lift-out, the chunk is lifted out of the bulk sample at 110° stage rotation."

    // Milling Angle Calculator Page
    readonly property string millingAngleLabelTooltip: "FIB milling angle (deg.) relative to the sample surface. Editing it sets the matching stage tilt angle."
    readonly property string stageTiltAngleLabelTooltip: "Stage tilt angle (deg.). This is the stored value; the milling angle is derived from it."
    readonly property string stageRotationLabelTooltip: "Stage rotation position the angles are calculated for. Switching position keeps the stage tilt and recomputes the milling angle."
}
