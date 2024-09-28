import React, { useState, useEffect } from "react"
import axios from "axios"
import { saveAs } from "file-saver" // For export
import * as XLSX from "xlsx" // For Excel export
import jsPDF from "jspdf" // For PDF export
import "jspdf-autotable" // For table in PDF

const InventoryPage = () => {
  const [assets, setAssets] = useState([])
  const [filteredAssets, setFilteredAssets] = useState([])
  const [filters, setFilters] = useState({
    date: "",
    name: "",
    department: "",
    classification: "",
    custodian: "",
  })
  const [currentPage, setCurrentPage] = useState(1)
  const itemsPerPage = 10

  useEffect(() => {
    const fetchAssets = async () => {
      try {
        const response = await axios.get("http://localhost:8000/api/assets/")
        setAssets(response.data)
        setFilteredAssets(response.data) // Initialize filtered assets
      } catch (error) {
        console.error("Error fetching the assets", error)
      }
    }
    fetchAssets()
  }, [])

  const indexOfLastItem = currentPage * itemsPerPage
  const indexOfFirstItem = indexOfLastItem - itemsPerPage
  const currentAssets = filteredAssets.slice(indexOfFirstItem, indexOfLastItem)

  const handleFilterChange = (e) => {
    setFilters({ ...filters, [e.target.name]: e.target.value })
  }

  const handleFilter = () => {
    const filtered = assets.filter((asset) => {
      return (
        (!filters.date ||
          new Date(asset.date_of_acquisition).toLocaleDateString() ===
            new Date(filters.date).toLocaleDateString()) &&
        (!filters.name ||
          asset.name.toLowerCase().includes(filters.name.toLowerCase())) &&
        (!filters.department ||
          asset.department
            .toLowerCase()
            .includes(filters.department.toLowerCase())) &&
        (!filters.classification ||
          asset.classification
            .toLowerCase()
            .includes(filters.classification.toLowerCase())) &&
        (!filters.custodian ||
          asset.custodian
            .toLowerCase()
            .includes(filters.custodian.toLowerCase()))
      )
    })
    setFilteredAssets(filtered)
    setCurrentPage(1) // Reset to page 1 after filtering
  }

  const handleClearFilters = () => {
    setFilters({
      date: "",
      name: "",
      department: "",
      classification: "",
      custodian: "",
    })
    setFilteredAssets(assets)
  }

  const handlePageChange = (pageNumber) => setCurrentPage(pageNumber)

  const exportToExcel = () => {
    const worksheet = XLSX.utils.json_to_sheet(filteredAssets)
    const workbook = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(workbook, worksheet, "Assets")
    XLSX.writeFile(workbook, "assets_inventory.xlsx")
  }

  const exportToCSV = () => {
    const worksheet = XLSX.utils.json_to_sheet(filteredAssets)
    const csv = XLSX.utils.sheet_to_csv(worksheet)
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" })
    saveAs(blob, "assets_inventory.csv")
  }

  const exportToPDF = () => {
    const doc = new jsPDF()
    doc.text("Assets Inventory", 14, 16)
    doc.autoTable({
      head: [["S/No", "Date", "Name", "Amount", "Dept", "Class", "Status"]],
      body: filteredAssets.map((asset, index) => [
        index + 1,
        new Date(asset.date_of_acquisition).toLocaleDateString(),
        asset.name,
        asset.amount,
        asset.department,
        asset.classification,
        asset.status,
      ]),
    })
    doc.save("assets_inventory.pdf")
  }

  const exportToJSON = () => {
    const json = JSON.stringify(filteredAssets, null, 2)
    const blob = new Blob([json], { type: "application/json;charset=utf-8;" })
    saveAs(blob, "assets_inventory.json")
  }

  return (
    <div className="p-8 bg-gray-50 min-h-screen">
      <h1 className="text-3xl font-bold mb-6 text-center text-green-700">
        Inventory of Assets
      </h1>

      {/* Filters */}
      <div className="mb-4 grid grid-cols-1 md:grid-cols-5 gap-4">
        <input
          type="date"
          name="date"
          value={filters.date}
          onChange={handleFilterChange}
          className="p-2 border rounded"
          placeholder="Filter by Date"
        />
        <input
          type="text"
          name="name"
          value={filters.name}
          onChange={handleFilterChange}
          className="p-2 border rounded"
          placeholder="Filter by Name"
        />
        <input
          type="text"
          name="department"
          value={filters.department}
          onChange={handleFilterChange}
          className="p-2 border rounded"
          placeholder="Filter by Department"
        />
        <input
          type="text"
          name="classification"
          value={filters.classification}
          onChange={handleFilterChange}
          className="p-2 border rounded"
          placeholder="Filter by Classification"
        />
        <input
          type="text"
          name="custodian"
          value={filters.custodian}
          onChange={handleFilterChange}
          className="p-2 border rounded"
          placeholder="Filter by Custodian"
        />
      </div>

      <div className="mb-4">
        <button
          onClick={handleFilter}
          className="px-4 py-2 bg-green-600 text-white rounded"
        >
          Apply Filters
        </button>
        <button
          onClick={handleClearFilters}
          className="px-4 py-2 bg-red-500 text-white rounded ml-2"
        >
          Clear Filters
        </button>
      </div>

      {/* Export buttons */}
      <div className="mb-4">
        <button
          onClick={exportToExcel}
          className="px-4 py-2 bg-blue-500 text-white rounded"
        >
          Export to Excel
        </button>
        <button
          onClick={exportToCSV}
          className="px-4 py-2 bg-blue-400 text-white rounded ml-2"
        >
          Export to CSV
        </button>
        <button
          onClick={exportToPDF}
          className="px-4 py-2 bg-red-500 text-white rounded ml-2"
        >
          Export to PDF
        </button>
        <button
          onClick={exportToJSON}
          className="px-4 py-2 bg-yellow-500 text-white rounded ml-2"
        >
          Export to JSON
        </button>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full bg-white border rounded-lg shadow-md">
          <thead>
            <tr className="bg-green-600 text-white">
              <th className="border px-6 py-3">Date of Acquisition</th>
              <th className="border px-6 py-3">Barcode</th>
              <th className="border px-6 py-3">Asset Name</th>
              <th className="border px-6 py-3">Amount</th>
              <th className="border px-6 py-3">Useful Life</th>
              <th className="border px-6 py-3">Department</th>
              <th className="border px-6 py-3">Classification</th>
              <th className="border px-6 py-3">Custodian</th>
              <th className="border px-6 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {currentAssets.length === 0 ? (
              <tr>
                <td
                  colSpan="10"
                  className="text-center py-8 text-gray-500 font-medium"
                >
                  No assets found in the inventory.
                </td>
              </tr>
            ) : (
              currentAssets.map((asset, index) => (
                <tr
                  key={asset.id}
                  className={`${
                    index % 2 === 0 ? "bg-gray-100" : "bg-white"
                  } hover:bg-gray-200`}
                >
                  <td className="border px-6 py-4 text-center">
                    {index + 1 + indexOfFirstItem}
                  </td>
                  <td className="border px-6 py-4 text-center">
                    {new Date(asset.date_of_acquisition).toLocaleDateString()}
                  </td>
                  <td className="border px-6 py-4 text-center">{asset.id}</td>
                  <td className="border px-6 py-4 text-left">{asset.name}</td>
                  <td className="border px-6 py-4 text-right">
                    ${asset.amount.toFixed(2)}
                  </td>
                  <td className="border px-6 py-4 text-center">
                    {asset.useful_life}
                  </td>
                  <td className="border px-6 py-4 text-left">
                    {asset.department}
                  </td>
                  <td className="border px-6 py-4 text-left">
                    {asset.classification}
                  </td>
                  <td className="border px-6 py-4 text-left">
                    {asset.custodian}
                  </td>
                  <td className="border px-6 py-4 text-left">{asset.status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="mt-6 flex justify-center">
        {Array.from(
          { length: Math.ceil(filteredAssets.length / itemsPerPage) },
          (_, index) => (
            <button
              key={index}
              onClick={() => handlePageChange(index + 1)}
              className={`px-4 py-2 border ${
                currentPage === index + 1
                  ? "bg-green-600 text-white"
                  : "bg-white text-green-600"
              } mx-1 rounded`}
            >
              {index + 1}
            </button>
          )
        )}
      </div>
    </div>
  )
}

export default InventoryPage
