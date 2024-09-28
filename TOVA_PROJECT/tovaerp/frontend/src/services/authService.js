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
      throw error
    }
  },
  login: async (email, password) => {
    try {
      const response = await axiosInstance.post("auth/login/", {
        email, // or 'username', depending on your backend setup
        password,
      })
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


