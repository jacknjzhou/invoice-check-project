import { BrowserRouter, Routes, Route, Navigate, Link, useLocation } from 'react-router-dom'
import { Layout, Menu } from 'antd'
import {
  UploadOutlined,
  FileTextOutlined,
  BarChartOutlined,
  MessageOutlined,
  SettingOutlined,
} from '@ant-design/icons'
import UploadPage from './pages/UploadPage'
import InvoiceListPage from './pages/InvoiceListPage'
import InvoiceDetailPage from './pages/InvoiceDetailPage'
import ReportPage from './pages/ReportPage'
import ChatPage from './pages/ChatPage'
import SettingsPage from './pages/SettingsPage'

const { Header, Content, Sider } = Layout

const menuItems = [
  { key: '/upload', icon: <UploadOutlined />, label: <Link to="/upload">上传发票</Link> },
  { key: '/invoices', icon: <FileTextOutlined />, label: <Link to="/invoices">发票列表</Link> },
  { key: '/report', icon: <BarChartOutlined />, label: <Link to="/report">报表统计</Link> },
  { key: '/chat', icon: <MessageOutlined />, label: <Link to="/chat">智能问答</Link> },
  { key: '/settings', icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> },
]

function AppLayout() {
  const location = useLocation()
  const selectedKey = menuItems.find(item => location.pathname.startsWith(item.key))?.key || '/upload'

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider theme="light" width={200}>
        <div style={{ padding: '16px', fontWeight: 'bold', fontSize: '18px', textAlign: 'center' }}>
          发票整理助手
        </div>
        <Menu mode="inline" selectedKeys={[selectedKey]} items={menuItems} />
      </Sider>
      <Layout>
        <Content style={{ padding: '24px', margin: 0, minHeight: 280 }}>
          <Routes>
            <Route path="/" element={<Navigate to="/upload" replace />} />
            <Route path="/upload" element={<UploadPage />} />
            <Route path="/invoices" element={<InvoiceListPage />} />
            <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
            <Route path="/report" element={<ReportPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <AppLayout />
    </BrowserRouter>
  )
}
