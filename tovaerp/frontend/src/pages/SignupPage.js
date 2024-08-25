import React, { useState } from "react"
import { useNavigate } from "react-router-dom"
import AuthService from "../services/authService"

const SignupPage = () => {
  const [username, setUsername] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [error, setError] = useState("")
  const [passwordRequirements, setPasswordRequirements] = useState({
    length: false,
    uppercase: false,
    lowercase: false,
    digit: false,
    specialChar: false,
  })
  const navigate = useNavigate()

  const handleSignup = async (e) => {
    e.preventDefault()
    if (password !== confirmPassword) {
      setError("Passwords do not match!")
      return
    }
    try {
      const userData = {
        username: username,
        email: email,
        password1: password,
        password2: confirmPassword,
      }
      const response = await AuthService.register(userData)
      if (response.status === 201) {
        navigate("/login")
      }
    } catch (err) {
      setError("Registration failed. Try again.")
    }
  }

  const handlePasswordChange = (e) => {
    const password = e.target.value
    setPassword(password)
    setPasswordRequirements({
      length: password.length >= 6,
      uppercase: /[A-Z]/.test(password),
      lowercase: /[a-z]/.test(password),
      digit: /\d/.test(password),
      specialChar: /[!@#$%^&*()_+{}\[\]:;"'<>,.?/\\-]/.test(password),
    })
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-100">
      <div className="bg-white p-8 rounded shadow-md">
        <h1 className="text-2xl font-bold mb-4">Signup</h1>
        {error && <p className="text-red-500">{error}</p>}
        <form onSubmit={handleSignup}>
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
            onChange={handlePasswordChange}
          />
          <input
            type="password"
            placeholder="Confirm Password"
            className="block w-full mb-4 p-2 border border-gray-300 rounded"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          <div className="mb-4">
            <p
              className={`text-sm ${
                passwordRequirements.length ? "text-green-500" : "text-red-500"
              }`}
            >
              Password must be at least 6 characters long
            </p>
            <p
              className={`text-sm ${
                passwordRequirements.uppercase
                  ? "text-green-500"
                  : "text-red-500"
              }`}
            >
              Must contain at least one uppercase letter
            </p>
            <p
              className={`text-sm ${
                passwordRequirements.lowercase
                  ? "text-green-500"
                  : "text-red-500"
              }`}
            >
              Must contain at least one lowercase letter
            </p>
            <p
              className={`text-sm ${
                passwordRequirements.digit ? "text-green-500" : "text-red-500"
              }`}
            >
              Must contain at least one digit
            </p>
            <p
              className={`text-sm ${
                passwordRequirements.specialChar
                  ? "text-green-500"
                  : "text-red-500"
              }`}
            >
              Must contain at least one special character
            </p>
          </div>
          <button className="w-full bg-green-500 text-white p-2 rounded">
            Signup
          </button>
        </form>
      </div>
    </div>
  )
}

export default SignupPage
