import React, { useState, useEffect } from "react"
import axios from "axios"

const InventoryPage = () => {
  const [assets, setAssets] = useState([])

  useEffect(() => {
    const fetchAssets = async () => {
      try {
        const response = await axios.get("http://localhost:8000/api/assets/")
        setAssets(response.data)
      } catch (error) {
        console.error("Error fetching the assets", error)
      }
    }
    fetchAssets()
  }, [])

  return (
    <div className="p-8">
      <h1 className="text-3xl font-bold mb-4">Inventory of Assets</h1>
      <table className="min-w-full bg-white border">
        <thead>
          <tr>
            <th className="border px-4 py-2">S/No</th>
            <th className="border px-4 py-2">Description</th>
            <th className="border px-4 py-2">Date of Acquisition</th>
            <th className="border px-4 py-2">Barcode</th>
            <th className="border px-4 py-2">Department</th>
            {/* Add more table headers as necessary */}
          </tr>
        </thead>
        <tbody>
          {assets.map((asset, index) => (
            <tr key={asset.id}>
              <td className="border px-4 py-2">{index + 1}</td>
              <td className="border px-4 py-2">{asset.description}</td>
              <td className="border px-4 py-2">{asset.date_of_acquisition}</td>
              <td className="border px-4 py-2">{asset.barcode}</td>
              <td className="border px-4 py-2">{asset.department}</td>
              {/* Add more table data as necessary */}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default InventoryPage

