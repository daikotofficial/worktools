import React from "react"
import { Link } from "react-router-dom"
import "./WelcomePage.css" // Add styles to make it look good

const WelcomePage = () => {
  return (
    <div className="welcome-container">
      <h1>Welcome to TOVAERP</h1>
      <p>Manage your company's assets efficiently.</p>
      <div className="welcome-buttons">
        <Link to="/login" className="btn btn-primary">
          Login
        </Link>
        <Link to="/signup" className="btn btn-secondary">
          Sign Up
        </Link>
      </div>
    </div>
  )
}

export default WelcomePage

