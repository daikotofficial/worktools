// src/services/authService.js

import axiosInstance from "./axiosInstance"

const authService = {
  register: async (userData) => {
    try {
      return await axiosInstance.post("auth/registration/", userData)
    } catch (error) {
      console.error(
        "Registration error:",
        error.response ? error.response.data : error.message
      )
      throw error // Re-throw to handle in the component
    }
  },
  login: async (email, password) => {
    try {
      const response = await axiosInstance.post("auth/login/", {
        email,
        password,
      })
      if (!response.data.is_active) {
        throw new Error("Email not verified. Please check your inbox.")
      }
      return response
    } catch (error) {
      console.error(
        "Login error:",
        error.response ? error.response.data : error.message
      )
      throw error // Re-throw to handle in the component
    }
  },
}

export default authService

