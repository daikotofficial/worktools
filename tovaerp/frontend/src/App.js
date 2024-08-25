import React from "react"
import { BrowserRouter as Router, Route, Routes } from "react-router-dom"
import LoginPage from "./pages/LoginPage"
import SignupPage from "./pages/SignupPage"
import Dashboard from "./pages/Dashboard"
import AddAssetPage from "./pages/AddAssetPage"
import InventoryPage from "./pages/InventoryPage"
import AssignUserPage from "./pages/AssignUserPage"
import WelcomePage from "./pages/WelcomePage"

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/add-asset" element={<AddAssetPage />} />
        <Route path="/inventory" element={<InventoryPage />} />
        <Route path="/assign-user" element={<AssignUserPage />} />
        {/* Add more routes as needed */}
      </Routes>
    </Router>
  )
}

export default App
