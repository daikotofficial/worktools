import React, { useState } from "react"
import { Link } from "react-router-dom"

const Dashboard = () => {
  const [notifications] = useState(3) // Example notifications count

  // Dummy user profile image, replace with real uploaded user profile image
  const userProfileImage = "/images/tovaERPtxt.jpg"

  return (
    <div className="dashboard-container">
      {/* Main Section */}
      <div className="main-section">
        {/* Top Navigation */}
        <header className="top-nav">
          <div className="search-bar">
            <input type="text" placeholder="Search..." />
          </div>
          <div className="top-nav-right">
            <Link to="/notifications">
              <i className="icon-bell"></i>
              {notifications > 0 && (
                <span className="notification-count">{notifications}</span>
              )}
            </Link>
            <div className="profile-dropdown">
              <img
                src={userProfileImage}
                alt="User Profile"
                className="profile-image"
              />
              {/* Dropdown for profile */}
              <div className="dropdown-content">
                <Link to="/edit-profile">Edit Profile</Link>
                <Link to="/logout">Logout</Link>
              </div>
            </div>
          </div>
        </header>

        {/* Dashboard Content */}
        <div className="dashboard-content">
          {/* Stats Cards */}
          <div className="stats-cards">
            <div className="stats-card">
              <div className="stats-content">
                <h3>Total Assets</h3>
                <p>450</p>
                <span>45% Increase in 28 Days</span>
              </div>
            </div>
            <div className="stats-card">
              <div className="stats-content">
                <h3>Total Assets Value</h3>
                <p>105,000,890</p>
                <span>60% Increase in 28 Days</span>
              </div>
            </div>
            <div className="stats-card">
              <div className="stats-content">
                <h3>Net Book Value</h3>
                <p>122</p>
                <span>45% of total Assets</span>
              </div>
            </div>
            <div className="stats-card">
              <div className="stats-content">
                <h3>Accumulated Depreciation</h3>
                <p>132,125</p>
                <span>80% of total Assets</span>
              </div>
            </div>
          </div>

          {/* Graphs Section */}
          <div className="graphs-section">
            <div className="graph-card">
              <h3>Asset Distribution</h3>
              <div className="graph-placeholder">
                <p>Graph Placeholder</p>
              </div>
            </div>
            <div className="graph-card">
              <h3>Asset Growth</h3>
              <div className="graph-placeholder">
                <p>Graph Placeholder</p>
              </div>
            </div>
          </div>
        </div>

        {/* Footer Section */}
        <footer className="dashboard-footer">
          <p>&#169; 2024 Tova. All rights reserved.</p>
          <div className="footer-links">
            <Link to="/privacy-policy">Privacy Policy</Link> |{" "}
            <Link to="/terms-of-service">Terms of Service</Link> |{" "}
            <Link to="/data-protection">Data Protection</Link>
          </div>
        </footer>
      </div>
    </div>
  )
}

export default Dashboard


