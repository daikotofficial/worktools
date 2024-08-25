import axiosInstance from "./axiosInstance" // Correct path to axiosInstance

const register = async (userData) => {
  try {
    return await axiosInstance.post("auth/registration/", userData)
  } catch (error) {
    console.error(
      "Registration error:",
      error.response ? error.response.data : error.message
    )
    throw error // Re-throw to handle in the component
  }
}

const login = async (email, password) => {
  try {
    return await axiosInstance.post("auth/login/", { email, password })
  } catch (error) {
    console.error(
      "Login error:",
      error.response ? error.response.data : error.message
    )
    throw error // Re-throw to handle in the component
  }
}

export default {
  register,
  login,
}
