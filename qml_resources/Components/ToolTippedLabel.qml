import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "../Config"

Label {

    id: root

    property string toolTipText: ""

    font.pixelSize: AppConfig.pageBodyFontSize
    Layout.alignment: Qt.AlignLeft | Qt.AlignVCenter

    HoverHandler { id: labelHover }

    ToolTip.text: root.toolTipText
    ToolTip.visible: root.toolTipText !== "" && labelHover.hovered
    ToolTip.delay: AppConfig.toolTipDelayMs
    ToolTip.timeout: AppConfig.toolTipTimeoutMs

}
