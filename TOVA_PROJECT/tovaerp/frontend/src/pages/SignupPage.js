// src/pages/SignupPage.js

import React, { useState } from "react"
import { useNavigate } from "react-router-dom"
import AuthService from "../services/authService"

const SignupPage = () => {
  const [username, setUsername] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [companyName, setCompanyName] = useState("")
  const [phoneNumber, setPhoneNumber] = useState("")
  const [address, setAddress] = useState("")
  const [logo, setLogo] = useState(null)
  const [sector, setSector] = useState("")
  const [error, setError] = useState("")
  const [message, setMessage] = useState("")
  const navigate = useNavigate()

  const handleSignup = async (e) => {
    e.preventDefault()
    if (password !== confirmPassword) {
      setError("Passwords do not match!")
      return
    }
    try {
      const formData = new FormData()
      formData.append("username", username)
      formData.append("email", email)
      formData.append("password1", password)
      formData.append("password2", confirmPassword)
      formData.append("company_name", companyName)
      formData.append("phone_number", phoneNumber)
      formData.append("address", address)
      formData.append("logo", logo)
      formData.append("sector", sector)

      const response = await AuthService.register(formData)
      if (response.status === 201) {
        setMessage(
          "Registration successful! Please check your email to confirm your account."
        )
      }
    } catch (err) {
      setError("Registration failed. Try again.")
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow-md">
        <h1 className="text-2xl font-bold mb-4">Signup</h1>
        {error && <p className="text-red-500">{error}</p>}
        {message && <p className="text-green-500">{message}</p>}
        <form onSubmit={handleSignup} encType="multipart/form-data">
          <input
            type="text"
            placeholder="Username"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            type="email"
            placeholder="Email"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            type="password"
            placeholder="Password"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <input
            type="password"
            placeholder="Confirm Password"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          <input
            type="text"
            placeholder="Company Name"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
          />
          <input
            type="text"
            placeholder="Phone Number"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
          />
          <input
            type="text"
            placeholder="Address"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
          <input
            type="file"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            onChange={(e) => setLogo(e.target.files[0])}
          />
          <input
            type="text"
            placeholder="Sector/Nature of Service"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={sector}
            onChange={(e) => setSector(e.target.value)}
          />
          <button className="w-full bg-green-500 text-white p-2 rounded">
            Signup
          </button>
        </form>
      </div>
    </div>
  )
}

export default SignupPage

