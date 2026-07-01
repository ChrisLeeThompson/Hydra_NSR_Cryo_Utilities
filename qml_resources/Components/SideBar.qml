import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Universal
import QtQuick.Layouts
import "../Config"

// SideBar navigation with links for the main pages and secondary pages, such as "Settings".

Rectangle {

    id: root

    property int currentPageIndex: 0
    property alias primaryModel: primaryRepeater.model
    property alias secondaryModel: secondaryRepeater.model

    signal pageSelected(int index)

    color: AppConfig.sideBarBackground

    component NavItem: ItemDelegate {

        required property int index
        required property string name
        property int indexOffset: 0

        readonly property int globalIndex: index + indexOffset

        Layout.fillWidth: true
        text: name
        font.pixelSize: AppConfig.navItemFontSize
        highlighted: root.currentPageIndex === globalIndex
        onClicked: {
            root.currentPageIndex = globalIndex
            root.pageSelected(globalIndex)
        }

    }

    ColumnLayout {

        anchors.fill: parent
        anchors.margins: AppConfig.sideBarMargins
        spacing: AppConfig.sideBarColumnSpacing

        Repeater {

            id: primaryRepeater
            delegate: NavItem { }

        }

        Item { Layout.fillHeight: true }

        Repeater {

            id: secondaryRepeater
            delegate: NavItem { indexOffset: primaryRepeater.count }

        }

    }

}
