import React, { useState } from "react"
import { useNavigate } from "react-router-dom"
import AuthService from "../services/authService"
import FourEllipsesSpinner from "../components/FourEllipsesSpinner"

const LoginPage = () => {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()

  const handleLogin = async (e) => {
    e.preventDefault()
    setLoading(true) // Set loading to true during the login request
    try {
      const response = await AuthService.login(email, password)
      localStorage.setItem("user", JSON.stringify(response.data)) // Store user data locally
      navigate("/dashboard") // Redirect to dashboard after successful login
    } catch (error) {
      // Handle different errors like unverified email or wrong credentials
      if (error.response && error.response.data && error.response.data.detail) {
        setError(error.response.data.detail) // Display server error response
      } else {
        setError("Login failed. Please check your credentials.")
      }
    } finally {
      setLoading(false) // Set loading to false once login is complete
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow-md">
        <h1 className="text-2xl font-bold mb-4">Login</h1>
        {error && <p className="text-red-500">{error}</p>} {/* Display error */}
        {loading ? (
          <FourEllipsesSpinner /> // Show spinner while loading
        ) : (
          <form onSubmit={handleLogin}>
            <input
              type="email"
              placeholder="Email"
              className="block w-full mb-4 p-2 border border-gray-300 rounded"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
            <input
              type="password"
              placeholder="Password"
              className="block w-full mb-4 p-2 border border-gray-300 rounded"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
            <button className="w-full bg-green-500 text-white p-2 rounded">
              Login
            </button>
          </form>
        )}
      </div>
    </div>
  )
}

export default LoginPage


