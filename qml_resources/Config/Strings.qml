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
    readonly property string stageMoveErrorConfirmDialog: "Error during stage move. Please refer to the console logs for more information."

        // Generic confirm-dialog button defaults (overridable per instance)
    readonly property string dialogDefaultAcceptText: "Ok"
    readonly property string dialogDefaultRejectText: "Cancel"

        // Pre-start check dialogs (main.qml)
    readonly property string preStartRefuseTitle: "Cannot start"
    readonly property string preStartConfirmTitle: "Acknowledge safety check"
    readonly property string preStartConfirmAcceptText: "Acknowledge and Continue"

        // Stage positions dialogs (%1 = position name)
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
    readonly property string templateLoadPositionsHint: "This template includes saved stage positions.\n\nNew positions are added to the list; existing positions are not overwritten."
    readonly property string templateLoadContinueText: "Load"

        // Session log (Settings.qml)
    readonly property string clearSessionLogTitle: "Clear Session Log?"
    readonly property string clearSessionLogMessage: "All logged sessions, including added notes, will be deleted. The session log file will be replaced with an empty one."

    // Tooltips
        // Activities
    readonly property string gisPurgeActivityTooltip: "Purges the Pt GIS for the specified duration."
    readonly property string homeStageActivityTooltip: "Performs a home stage procedure with rotation.\n\nThe home stage procedure cannot be stopped with the script. A manual intervention is required to stop the procedure."
    readonly property string stagePositionsActivityTooltip: "Saved stage positions used in the GIS Deposition activities."
    readonly property string gisDepositionActivityTooltip: "Deposits/condenses material on the grid using the GIS at the selected stage position."
    readonly property string removeActivityButtonTooltip: "Remove this activity from the list."
    readonly property string addSputterCoatActivityTooltip: "Adds a sputter coat activity to the list."
    readonly property string addGISDepositionActivityTooltip: "Adds a GIS deposition activity to the list."
    readonly property string chamberRecoveryLabelTooltip: "Vacuum chamber recovery time (seconds) after "
                                                          + "the activity completes.\nThe script will pause for the specified duration and allow the chamber pressure to recover."

        // GIS purge activity
    readonly property string gisPurgeDurationLabelTooltip: "GIS valve will open and purge for the specified duration (seconds)."

        // Cryo prep page
    readonly property string saveButtonTooltip: "Save the current activity list and, optionally, stage activity positions as a template file."
    readonly property string loadButtonTooltip: "Load activities and parameters from a template file.\n"
                                                + "The current activity list will be replaced."

        // Stage positions activity
    readonly property string stageAddPositionButtonTooltip: "Add the current stage position to the list."
    readonly property string stageEditNameButtonTooltip: "Edit the name of the selected position."
    readonly property string stageMoveToButtonTooltip: "Move the stage to the selected position."
    readonly property string stageUpdatePositionButtonTooltip: "Overwrite the selected position with the current stage position."
    readonly property string stageRemovePositionButtonTooltip: "Remove the selected position from the list."

        // Sputter coat activity
    readonly property string sputterCoatActivityTooltip: "Sputter coats at the selected stage position by applying the configured pattern file with the µCoater GIS and PFIB."
    readonly property string ionSpeciesLabelTooltip: "Plasma gas species used for sputtering."
    readonly property string ionCurrentLabelTooltip: "Ion beam current used for sputtering. Available values depend on "
                                                     + "the selected ion species."
    readonly property string highVoltageLabelTooltip: "PFIB high voltage (kV) used for sputtering."
    readonly property string sputterCoatDurationLabelTooltip: "Sputter coat duration (seconds)."
    readonly property string bulkSputterCoatDurationButtonTooltip: "Set duration to " + AppConfig.bulkSputterCoatDefaultDuration
                                                                   + " s (bulk sputtering preset)."
    readonly property string lamellaSputterCoatDurationButtonTooltip: "Set duration to " + AppConfig.lamellaSputterCoatDefaultDuration
                                                                      + " s (lamella sputtering preset)."

        // Position-based activities (GIS deposition, sputter coat)
    readonly property string positionLabelTooltip: "Stage position to move to before running the activity."
    readonly property string gisDepositionDurationLabelTooltip: "Number of seconds the GIS valve is open."

        // Stage / Scan page
    readonly property string stageRot180ButtonTooltip: "Rotates the stage 180° (relative and compucentric)."
    readonly property string setScanRot0ButtonTooltip: "Set the scan rotation of the SEM and FIB to C."
    readonly property string setScanRot180ButtonTooltip: "Set the scan rotation of the SEM and FIB to 180°."
    readonly property string scanRotWithStageCheckBoxTooltip: "After the stage is rotated 180°, the SEM and FIB are scan rotated 180°."
    readonly property string stageTiltWithRotCheckBoxTooltip: "Recommended for safe stage rotations. The stage tilts to 0° before rotation. After rotation, the stage is tilted to the original angle.\n\nIf Stage Tilt After Rotation is enabled, the stage will tilt to the specified angle after rotation."
    readonly property string tiltAfterRotationCheckBoxTooltip: "After rotation, the stage is tilted to the specified angle (degrees).\n\nIf Zero Tilt Before Rotation is enabled, then the stage is tilted to 0° before rotation.\n\nIf Zero Tilt Before Rotation is not enabled, then the stage will rotate with the current tilt angle."
    readonly property string seventeenLabelTooltip: "Click to set the Tilt After Rotation value to 17."
    readonly property string thirtyFiveLabelTooltip: "Click to set the Tilt After Rotation value to 35."
    readonly property string stageZSliderLabelTooltip: "Move the slider, or click on the slider groove, to adjust the z height of the stage. The step size increases as the slider handle is moved further from the center."
        // Stage Z slider safety gate
    readonly property string stageZSafetyTitle: "Stage position outside safe range\n"
    readonly property string stageZSafetyUnlockButton: "Unlock"

        // Settings
    readonly property string alwaysOnTopLabelTooltip: "Keep the UI window above all other applications."
    readonly property string compactModeLabelTooltip: "Snap the window to its compact size (subtitles hidden, headers tightened); uncheck to restore the normal size. The checkbox mirrors the live layout - manually resizing the window toggles it too. Compact mode with Always On Top can be useful when using the Stage/Scan page and placing the UI over a quadrant in xT Microscope Control."
    readonly property string gisGasPortNameLabelTooltip: "The name of the GIS gas port used in the GIS Purge and GIS Deposition activities."
    readonly property string sputterPatternFileLabelTooltip: "File name of the .ptf sputter pattern to apply, located in the pattern_files folder."
    readonly property string sputterCoatHfwLabelTooltip: "The PFIB horizontal field width (microns) used during sputter coating."
    readonly property string sputterCoatPortNameLabelTooltip: "The name of the GIS port used as the sputter coater (e.g. µCoater)."
    readonly property string bulkSputteringDefaultDurationLabelTooltip: "The default duration (seconds) for the Bulk Sputtering button in the Sputter Coat activity."
    readonly property string lamellaSputteringDefaultDurationLabelTooltip: "The default duration (seconds) for the Lamella Sputtering button in the Sputter Coat activity."
    readonly property string zeroTiltBeforeGisDepositionLabelTooltip: "The stage is tilted to 0° before moving to the GIS deposition position."
    readonly property string moveStageToOriginalPositionLabelTooltip: "Move the stage to its original position after all RT Prep or Cryo Prep activites have successfully completed."
    readonly property string restoreOriginalPFIBVoltageAndCurrentLabelTooltip: "Restore the original PFIB high voltage, beam current, and on/off state after all Cryo Prep activites have completed."
    readonly property string restoreOriginalPFIBIonSpeciesLabelTooltip: "Restore the original PFIB ion species after all Cryo Prep activities have completed."
    readonly property string resetRTPrepParametersLabelTooltip: "Reset RT Prep page activity parameters to their default values."
    readonly property string resetCryoPrepParametersLabelTooltip: "Reset Cryo Prep page activity parameters to their default values."
    readonly property string sessionLogSizeLabelTooltip: "Disk size of the session log file. Use the Clear Session Log button to reset it."
    readonly property string clearSessionLogLabelTooltip: "Delete all session log entries. The session log file is replaced with an empty one."

        // Workflow buttons
    readonly property string startButtonTooltip: "Start the workflow.\n\n"
                                                  + "Enabled when at least one activity is switched on, "
                                                  + "the microscope is connected, and no other workflow is running."
    readonly property string stopButtonTooltip: "Stop the workflow at the next available beakpoint."
    readonly property string resetParametersButtonTooltip: "Reset activity parameters to their default values."

        // Lift-out Calculator
    readonly property string shuttlePretiltAngleLabelToolTip: "Shuttle pre-tilt angle (degrees)."
    readonly property string liftoutTiltAngleLabelTooltip: "Stage tilt angle (degrees) when attaching the EasyLift needle to the chunk."
    readonly property string attachTiltAngleLabelToolTip: "Stage tilt angle (degrees) when attaching the chunk to the lift-in grid (TEM grid)."
    readonly property string fibStageTiltAngleLabelToolTip: "Stage tilt angle (degrees) for FIB milling the chunk at the –70° stage rotation position (lamella milling position)."
    readonly property string fibMillingAngleLabelToolTip: "FIB milling angle (degrees) when the shuttle is at ~ -70° stage rotation."
    readonly property string semTiltAngleLabelToolTip: "Stage tilt angle (degrees) for positioning the chunk/lamella perpendicular to the SEM when the shuttle is at ~ -70° stage rotation. Formula: pre-tilt angle + fib milling angle."
    readonly property string liftoutCalcResetButtonTooltip: "Reset calculator to default values."

    // Lift-out Calculator descriptions
    readonly property string liftoutDescTopdownMinus70: "<b>Top-down lift-out: –70° rotation</b><br><br>"
                                                        + "Top-down lift-out allows for perpendicular milling angles relative to the surface of the bulk sample."
                                                        + " –70° rotation refers to the standard stage rotation position for milling lamella with an AutoGrid shuttle."
                                                        + " For this type of lift-out, the lift-out of the chunk from the bulk sample is performed at –70° stage rotation."
    readonly property string liftoutDescPlanarMinus70: "<b>Planar lift-out: –70° rotation</b><br><br>"
                                                        + "Planar lift-out allows for parallel milling angles relative to the surface of the bulk sample."
                                                        + " –70° rotation refers to the standard stage rotation position for milling lamella with an AutoGrid shuttle."
                                                        + " For this type of lift-out, the lift-out of the chunk from the bulk sample is performed at –70° stage rotation."
    readonly property string liftoutDescPlanar110: "<b>Planar lift-out: 110° rotation</b><br><br>"
                                                        + "Planar lift-out allows for parallel milling angles relative to the surface of the bulk sample."
                                                        + " 110° rotation refers to the stage rotation position that is 180° rotated from the –70° rotation position with an AutoGrid shuttle."
                                                        + " For this type of lift-out, the lift-out of the chunk from the bulk sample is performed at 110° stage rotation."

    // Milling Angle Calculator Page
    readonly property string millingAngleLabelTooltip: ""
    readonly property string stageTiltAngleLabelTooltip: ""
    readonly property string stageRotationLabelTooltip: ""
}
