import React, { useState } from "react"
import axios from "axios"

const AddAssetPage = () => {
  const [asset, setAsset] = useState({
    description: "",
    vendor: "",
    nature_of_acquisition: "",
    custodian: "",
    condition: "",
    date_of_acquisition: "",
    serial_number: "",
    useful_life: "",
    location: "",
    scrap_value: "",
    barcode: "",
    invoice_number: "",
    classification: "",
    department: "",
    cost: "",
  })

  const handleChange = (e) => {
    setAsset({
      ...asset,
      [e.target.name]: e.target.value,
    })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    try {
      const response = await axios.post(
        "http://localhost:8000/api/assets/",
        asset
      )
      if (response.status === 201) {
        alert("Asset added successfully!")
      }
    } catch (error) {
      console.error("There was an error adding the asset!", error)
    }
  }

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Add New Asset</h1>
      <form onSubmit={handleSubmit}>
        {/* Add input fields for all asset properties */}
        <input
          type="text"
          name="description"
          placeholder="Description"
          className="block w-full mb-4 p-2 border border-gray-300 rounded"
          onChange={handleChange}
        />
        {/* Add more input fields here */}
        <button className="bg-green-500 text-white p-2 rounded">
          Add Asset
        </button>
      </form>
    </div>
  )
}

export default AddAssetPage

