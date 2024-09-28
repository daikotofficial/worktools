// src/pages/AssignUserPage.js
import React, { useState } from "react"
import axios from "axios"

const AssignUserPage = () => {
  const [user, setUser] = useState({
    fullName: "",
    username: "",
    password: "",
    role: "",
    phone: "",
    email: "",
    location: "",
  })

  const handleChange = (e) => {
    setUser({
      ...user,
      [e.target.name]: e.target.value,
    })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      const response = await axios.post(
        "http://localhost:8000/api/users/",
        user
      )
      if (response.status === 201) {
        alert("User assigned successfully!")
      }
    } catch (error) {
      console.error("There was an error assigning the user!", error)
    }
  }

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Assign User</h1>
      <form onSubmit={handleSubmit}>
        <input
          type="text"
          name="fullName"
          placeholder="Full Name"
          onChange={handleChange}
        />
        {/* Additional input fields */}
        <button className="bg-green-500 text-white p-2 rounded">
          Assign User
        </button>
      </form>
    </div>
  )
}

export default AssignUserPage


