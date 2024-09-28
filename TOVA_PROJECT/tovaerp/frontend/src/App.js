// src/App.js
import { BrowserRouter as Router, Routes, Route } from "react-router-dom"
import Sidebar from "./components/Sidebar"
import AddAssetPage from "./pages/AddAssetPage"
import AssignUserPage from "./pages/AssignUserPage"
import Dashboard from "./pages/Dashboard"
import InventoryPage from "./pages/InventoryPage"
import "./styles.css" // Import the central CSS

const App = () => {
  return (
    <Router>
      <div className="app-container">
        <Sidebar />
        <div className="main-content">
          <Routes>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/add-asset" element={<AddAssetPage />} />
            <Route path="/inventory" element={<InventoryPage />} />
            <Route path="/assign-user" element={<AssignUserPage />} />
          </Routes>
        </div>
      </div>
    </Router>
  )
}

export default App


