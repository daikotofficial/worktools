from django.db import models

class Location(models.Model):
    name = models.CharField(max_length=255)

    def __str__(self):
        return self.name

class Department(models.Model):
    name = models.CharField(max_length=255)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

class Asset(models.Model):
    description = models.CharField(max_length=255)
    vendor = models.CharField(max_length=255)
    acquisition_date = models.DateField()
    custodian = models.CharField(max_length=255)
    condition = models.CharField(max_length=50)
    serial_number = models.CharField(max_length=255, unique=True)
    useful_life = models.IntegerField()
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    department = models.ForeignKey(Department, on_delete=models.CASCADE)
    cost = models.DecimalField(max_digits=10, decimal_places=2)
    scrap_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    barcode = models.CharField(max_length=255, unique=True)
    classification = models.CharField(max_length=100)
    invoice_number = models.CharField(max_length=255)

    def __str__(self):
        return self.description
