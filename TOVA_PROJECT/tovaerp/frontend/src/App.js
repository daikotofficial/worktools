// src/App.js

import React from "react"
import { BrowserRouter as Router, Route, Routes } from "react-router-dom"
import LoginPage from "./pages/LoginPage"
import SignupPage from "./pages/SignupPage"
import Dashboard from "./pages/Dashboard"
import AddAssetPage from "./pages/AddAssetPage"
import InventoryPage from "./pages/InventoryPage"
import AssignUserPage from "./pages/AssignUserPage"
import WelcomePage from "./pages/WelcomePage"
import PrivateRoute from "./components/PrivateRoute"

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<WelcomePage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route
          path="/dashboard"
          element={
            <PrivateRoute>
              <Dashboard />
            </PrivateRoute>
          }
        />
        <Route
          path="/add-asset"
          element={
            <PrivateRoute>
              <AddAssetPage />
            </PrivateRoute>
          }
        />
        <Route
          path="/inventory"
          element={
            <PrivateRoute>
              <InventoryPage />
            </PrivateRoute>
          }
        />
        <Route
          path="/assign-user"
          element={
            <PrivateRoute>
              <AssignUserPage />
            </PrivateRoute>
          }
        />
      </Routes>
    </Router>
  )
}

export default App

