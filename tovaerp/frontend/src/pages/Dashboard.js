import React from "react"
import { Link } from "react-router-dom"

const Dashboard = () => {
  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold">Dashboard</h1>
      <div className="grid grid-cols-3 gap-4 mt-8">
        <Link
          to="/add-asset"
          className="bg-green-500 text-white p-4 rounded shadow-md"
        >
          Add Assets
        </Link>
        <Link
          to="/inventory"
          className="bg-gray-500 text-white p-4 rounded shadow-md"
        >
          Inventory of Assets
        </Link>
        <Link
          to="/assign-user"
          className="bg-black text-white p-4 rounded shadow-md"
        >
          Assign Users
        </Link>
        <Link
          to="/edit-asset"
          className="bg-blue-500 text-white p-4 rounded shadow-md"
        >
          Edit Assets
        </Link>
        <Link
          to="/transfer-asset"
          className="bg-yellow-500 text-white p-4 rounded shadow-md"
        >
          Transfer Assets
        </Link>
        <Link
          to="/evaluate-asset"
          className="bg-purple-500 text-white p-4 rounded shadow-md"
        >
          Evaluate Assets
        </Link>
        <Link
          to="/maintain-asset"
          className="bg-red-500 text-white p-4 rounded shadow-md"
        >
          Maintain Assets
        </Link>
        <Link
          to="/add-location"
          className="bg-teal-500 text-white p-4 rounded shadow-md"
        >
          Add New Location
        </Link>
      </div>
    </div>
  )
}

export default Dashboard

