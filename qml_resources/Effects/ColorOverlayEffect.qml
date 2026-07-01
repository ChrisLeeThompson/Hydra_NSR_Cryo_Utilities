// Created with Qt Quick Effect Maker (version 0.44), Mon May 26 14:21:02 2025

import QtQuick

Item {
    id: rootItem

    // This is the main source for the effect
    property Item source: null

    // This property defines the color value which is used to colorize the source.
    //
    property color colorOverlayColor: Qt.rgba(0, 0, 0, 1)

    ShaderEffect {
        readonly property alias iSource: rootItem.source
        readonly property alias colorOverlayColor: rootItem.colorOverlayColor

        vertexShader: 'color_overlay_effect.vert.qsb'
        fragmentShader: 'color_overlay_effect.frag.qsb'
        anchors.fill: parent
    }
}
