import React, { useState } from "react"
import axios from "axios"
import DatePicker from "react-datepicker"
import "react-datepicker/dist/react-datepicker.css"

const AddAssetPage = () => {
  // State for the asset
  const [asset, setAsset] = useState({
    company_abbreviation: "",
    name_of_asset: "",
    vendor: "",
    serial_number: "",
    nature_of_acquisition: "New",
    classification: "COE",
    custodian: "",
    amount: "",
    date_of_acquisition: new Date(),
    department: "",
    barcode: "",
  })

  // State for classification modal
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [newClassification, setNewClassification] = useState({
    classification_name: "",
    classification_abbreviation: "",
  })

  // State for classifications
  const [classifications, setClassifications] = useState([
    "PPE",
    "COE",
    "FF",
    "BG",
    "LD",
    "AE",
  ])

  // Handle input change for asset
  const handleChange = (e) => {
    setAsset({
      ...asset,
      [e.target.name]: e.target.value,
    })
  }

  // Handle classification change
  const handleClassificationChange = (e) => {
    if (e.target.value === "Add New") {
      setIsModalOpen(true)
    } else {
      setAsset({ ...asset, classification: e.target.value })
    }
  }

  // Handle form submit
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

  // Add new classification
  const addNewClassification = () => {
    setClassifications([
      ...classifications,
      newClassification.classification_abbreviation,
    ])
    setAsset({
      ...asset,
      classification: newClassification.classification_abbreviation,
    })
    setIsModalOpen(false)
    setNewClassification({
      classification_name: "",
      classification_abbreviation: "",
    })
  }

  // Format currency input
  const formatCurrency = (value) => {
    return value.replace(/\B(?=(\d{3})+(?!\d))/g, ",")
  }

  return (
    <div className="form-container">
      <h1 className="text-3xl font-bold mb-4 pt-sans-narrow-bold">
        Add New Asset
      </h1>
      <form onSubmit={handleSubmit} className="grid grid-cols-3 gap-6">
        {/* Company Abbreviation */}
        <div className="form-group">
          <label htmlFor="company_abbreviation">Company Abbreviation</label>
          <input
            type="text"
            name="company_abbreviation"
            id="company_abbreviation"
            placeholder="Company Abbreviation"
            value={asset.company_abbreviation}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Asset Name */}
        <div className="form-group">
          <label htmlFor="name_of_asset">Asset Name</label>
          <input
            type="text"
            name="name_of_asset"
            id="name_of_asset"
            placeholder="Asset Name"
            value={asset.name_of_asset}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Vendor */}
        <div className="form-group">
          <label htmlFor="vendor">Vendor</label>
          <input
            type="text"
            name="vendor"
            id="vendor"
            placeholder="Vendor"
            value={asset.vendor}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Serial Number */}
        <div className="form-group">
          <label htmlFor="serial_number">Serial Number</label>
          <input
            type="text"
            name="serial_number"
            id="serial_number"
            placeholder="Serial Number"
            value={asset.serial_number}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Nature of Acquisition */}
        <div className="form-group">
          <label htmlFor="nature_of_acquisition">Nature of Acquisition</label>
          <select
            name="nature_of_acquisition"
            id="nature_of_acquisition"
            value={asset.nature_of_acquisition}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          >
            <option value="New">New</option>
            <option value="Used">Used</option>
          </select>
        </div>

        {/* Classification */}
        <div className="form-group">
          <label htmlFor="classification">Classification</label>
          <select
            name="classification"
            id="classification"
            value={asset.classification}
            onChange={handleClassificationChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          >
            <option value="Add New">Add New</option>
            {classifications.map((classification) => (
              <option key={classification} value={classification}>
                {classification}
              </option>
            ))}
          </select>
        </div>

        {/* Custodian */}
        <div className="form-group">
          <label htmlFor="custodian">Custodian</label>
          <input
            type="text"
            name="custodian"
            id="custodian"
            placeholder="Who is in charge of this asset"
            value={asset.custodian}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Amount */}
        <div className="form-group">
          <label htmlFor="amount">Amount</label>
          <input
            type="text"
            name="amount"
            id="amount"
            placeholder="₦0.00"
            value={formatCurrency(asset.amount)}
            onChange={(e) =>
              setAsset({ ...asset, amount: e.target.value.replace(/,/g, "") })
            }
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Date of Acquisition */}
        <div className="form-group">
          <label htmlFor="date_of_acquisition">Date of Acquisition</label>
          <DatePicker
            selected={asset.date_of_acquisition}
            onChange={(date) =>
              setAsset({ ...asset, date_of_acquisition: date })
            }
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Department */}
        <div className="form-group">
          <label htmlFor="department">Department</label>
          <input
            type="text"
            name="department"
            id="department"
            placeholder="Department"
            value={asset.department}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>

        {/* Barcode */}
        <div className="form-group">
          <label htmlFor="barcode">Barcode</label>
          <input
            type="text"
            name="barcode"
            id="barcode"
            placeholder="Barcode"
            value={asset.barcode}
            onChange={handleChange}
            className="p-3 border rounded shadow-sm font-pt-sans-narrow"
          />
        </div>
      </form>

      {/* Add Asset button below form */}
      <div className="mt-6 col-span-3">
        <button type="submit" className="add-asset-btn p-3 w-full">
          Add Asset
        </button>
      </div>

      {/* Modal for adding new classification */}
      {isModalOpen && (
        <div
          className="modal-overlay"
          onClick={(e) => {
            // Close modal if clicked outside content
            if (e.target.classList.contains("modal-overlay")) {
              setIsModalOpen(false)
            }
          }}
        >
          <div className="modal-content">
            <h2 className="text-xl">Add New Classification</h2>
            <label htmlFor="classification_name">Classification Name</label>
            <input
              type="text"
              name="classification_name"
              id="classification_name"
              value={newClassification.classification_name}
              onChange={(e) =>
                setNewClassification({
                  ...newClassification,
                  classification_name: e.target.value,
                })
              }
              className="p-2 border rounded mt-1 mb-4"
            />
            <label htmlFor="classification_abbreviation">Abbreviation</label>
            <input
              type="text"
              name="classification_abbreviation"
              id="classification_abbreviation"
              value={newClassification.classification_abbreviation}
              onChange={(e) =>
                setNewClassification({
                  ...newClassification,
                  classification_abbreviation: e.target.value,
                })
              }
              className="p-2 border rounded mt-1 mb-4"
            />
            <button
              onClick={addNewClassification}
              className="p-2 bg-blue-500 text-white"
            >
              Save
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default AddAssetPage
