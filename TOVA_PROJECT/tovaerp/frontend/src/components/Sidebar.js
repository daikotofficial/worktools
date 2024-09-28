import React from "react"
import { Link } from "react-router-dom"
import {
  FaTachometerAlt,
  FaBox,
  FaEdit,
  FaExchangeAlt,
  FaWrench,
  FaCogs,
  FaLocationArrow,
  FaLayerGroup,
  FaUsersCog,
  FaFileImport,
} from "react-icons/fa"

const Sidebar = () => {
  return (
    <div className="sidebar open">
      {/* Web App Logo */}
      <div className="logo-container">
        <Link to="/dashboard">
          <img
            src="/images/tovaERPtxt.jpg"
            alt="WebApp Logo"
            className="logo"
          />
        </Link>
      </div>

      {/* Scrollable Sidebar Content */}
      <div className="sidebar-content">
        <ul className="sidebar-menu">
          <li className="menu-item">
            <Link to="/dashboard">
              <FaTachometerAlt />
              <span>Dashboard</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/inventory">
              <FaBox />
              <span>Assets Inventory</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/add-asset">
              <FaBox />
              <span>Add Asset</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/edit-asset">
              <FaEdit />
              <span>Edit Asset</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/transfer-asset">
              <FaExchangeAlt />
              <span>Transfer Asset</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/re-evaluate-asset">
              <FaWrench />
              <span>Re-evaluate Asset</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/maintain-assets">
              <FaCogs />
              <span>Maintain Assets</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/add-location">
              <FaLocationArrow />
              <span>Add Location</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/add-classification">
              <FaLayerGroup />
              <span>Add Classification</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/set-company-abbr">
              <FaCogs />
              <span>Set Company Abbreviation</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/import-assets">
              <FaFileImport />
              <span>Import Assets</span>
            </Link>
          </li>
          <li className="menu-item">
            <Link to="/add-users">
              <FaUsersCog />
              <span>Add Users</span>
            </Link>
          </li>
        </ul>
      </div>
    </div>
  )
}

export default Sidebar
