# The MIT License
#	
# Copyright (c) 2008 James Piechota
#	
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import sys
import re
import os.path

import ns.maya.msv.WReader as WReader

kTX, kTY, kTZ, kRX, kRY, kRZ = range(6)
channel2Enum = dict([["tx", kTX], ["ty", kTY], ["tz", kTZ], ["rx", kRX], ["ry", kRY], ["rz", kRZ]])
def isRotateEnum( channelEnum ):
	return channelEnum >= kRX

def isTranslateEnum( channelEnum ):
	return channelEnum < kRX

def formatAgentName( name, id="" ):
	'''Convenience function to make sure all of the agent
	   names are consistent. Sometimes they are read in
	   two parts, and sometimes that use a '.' as a delimeter,
	   etc...'''
	agentName = name
	if id:
		agentName = "%s_%s" % (name, id)
	agentName = re.sub('\.', '_', agentName)
	return agentName

class Material:
	def __init__(self):
		self.name = ""
		self.id = 0
		self.rawColorMap = ""
		self.specular = [ 0.0, 0.0, 0.0 ]
		self.specularVar = [ "", "", "" ]
		self.specularSpace = "hsv"
		self.ambient = [ 0.0, 0.0, 0.0 ]
		self.ambientVar = [ "", "", "" ]
		self.ambientSpace = "hsv"
		self.diffuse = [ 0.0, 0.0, 0.0 ]
		self.diffuseVar = [ "", "", "" ]
		self.diffuseSpace = "hsv"
		self.roughness = 0.02
		self.roughnessVar = ""

class Geometry:
	def __init__(self):
		self.name = ""
		self.file = ""
		self.id = 0
		self.material = 1
		self.weightsData = None
		self.attach = ""
	
	def deformers( self ):
		if self.weightsData:
			return self.weightsData.deformers
		else:
			return []
	
	def weights( self ):
		if self.weightsData:
			return self.weightsData.weights
		else:
			return[]

class Option:
	def __init__(self):
		self.name = ""
		self.var = ""
		self.inputs = []

class GeoDB:
	def __init__(self):
		self._byId = []
		self._byName = {}
		self._optioned = {}
	
	def geometryByName( self, name ):
		return self._byName[name]
	
	def geometryById( self, id ):
		return self._byId[id]
	
	def addGeometry(self, geometry):
 		numGeo = len(self._byId)
 		if geometry.id >= numGeo:
 			self._byId.extend([ None ] * (geometry.id - numGeo + 1))
 			
 		self._byId[geometry.id] = geometry
 		self._byName[geometry.name] = geometry
 		self._optioned[geometry.name] = geometry
 
	def addOption(self, option):
		for input in option.inputs:
			try:
				if not isinstance( self._optioned[input.name], Geometry ):
					continue
				self._optioned.pop(input.name)
			except:
				pass
			
		self._optioned[option.name] = option	


class GeoIter:
	def __init__(self, db, agent):
		self._db = db
		self._agent = agent
		self._list = db._optioned.values()
		self._last = len(self._list) - 1
		self._index = -1
		
	def __iter__(self):
		return self
	
	def next(self):
		if self._index >= self._last:
			raise StopIteration
		self._index += 1
		item = self._list[self._index]
		if isinstance( item, Option ):
			# item is an Option instance. Check the variable value and convert
			# it to the appropriate Geometry instance.
			# Some option nodes may use variables that can take more values
			# than there are options - make sure we don't get an index out
			# of bounds.
			#
			value = min(self._agent.variableValue( item.var, True ), len(item.inputs) - 1)
			item = item.inputs[value]
		
		assert isinstance(item, Geometry)
		return item
		

class Curve:
	def __init__(self, channel, type, numPoints):
		self.channel = channel
		self.type = type
		self.points = [()] * numPoints

class Action:
	def __init__(self):
		self.name = ""
		self.curves = {}
		self.maxPoints = 0
	
	def addCurve( self, curve ):
		if curve.type == "linear":
 		 	tokens = curve.channel.split(":")
 		 	if len(tokens) == 1:
 		 		object = ""
 		 		attr = tokens[0]
  		 	else:
 		 		object = tokens[0]
 		 		attr = tokens[1]
  		 		
 		 	if len(curve.points) > self.maxPoints:
 		 		self.maxPoints = len(curve.points)
 		 	 
  		 	self.curves[ "%s %s" % (object, attr) ] = curve

class Primitive:
	def __init__(self, joint):
		self.joint = joint
		self.rotate = [ 0.0, 0.0, 0.0 ]
		self.axis = [ False, True, False ]
		self.centre = [ 0.0, 0.0, 0.0 ]
					
class Tube(Primitive):
	def __init__(self, joint):
		Primitive.__init__(self, joint)
		self.radius = 1.0
		self.length = 1.0		

class Sphere(Primitive):
	def __init__(self, joint):
		Primitive.__init__(self, joint)
		self.radius = 1.0

class Box(Primitive):
	def __init__(self, joint):
		Primitive.__init__(self, joint)
		self.size = [ 1.0, 1.0, 1.0 ]
	
class Disc(Primitive):
	def __init__(self, joint):
		Primitive.__init__(self, joint)
		self.radius = 1.0
		self.length = 1.0

class Joint:
 	def __init__(self, agentDesc):
 		self.name = ""
 		self.parent = ""
 		self.dof = [ True ] * 6
 		self.primitive = None
 		self.order = [ kTX, kTY, kTZ, kRX, kRY, kRZ ]
 		self.translate = []
 		self.transform = []
		self.scaleVar = ""
 		self.agentDesc = agentDesc	

class Variable:
	def __init__(self):
		self.name = ""
		self.min = 0.0
		self.max = 0.0
		self.default = 0.0
		self.expression = ""


class SimJoint:
	def __init__(self):
		self.channels = dict([["tx", []], ["ty", []], ["tz", []], ["rx", []], ["ry", []], ["rz", []]])
		self.numFrames = 0
		self.startFrame = 0
	
	def addSample(self, frame, rotate, translate):
		index = (frame - self.startFrame)
		if index >= self.numFrames:
			extraFrames = (frame - self.numFrames + 1)
			if 1 == extraFrames:
				for channel in self.channels.values():
					channel.append( 0.0 )
			else:
				for channel in self.channels.values():
					channel.extend( [ 0.0 ] * extraFrames )
			self.numFrames = frame + 1
		self.channels["rx"][index] = rotate[0]
		self.channels["ry"][index] = rotate[1]
		self.channels["rz"][index] = rotate[2]
		self.channels["tx"][index] = translate[0]
		self.channels["ty"][index] = translate[1]
		self.channels["tz"][index] = translate[2]
		
		

class Sim:
	def __init__(self):
		self.options = {}
		self.joints = {}
		self.agentName = ""
		self.agentType = ""
		self.startFrame = None
		self.endFrame = None
	
	def addSample(self, frame, joint, rotate, translate ):
		jointData = None
		try:
			jointData = self.joints[joint]
		except:
			jointData = SimJoint()
			jointData.startFrame = frame
			self.joints[joint] = jointData
		
		jointData.addSample( frame, rotate, translate )
		if None == self.startFrame or frame < self.startFrame:
			self.startFrame = frame
		if None == self.endFrame or frame > self.endFrame:
			self.endFrame = frame

# Node definition
class AgentDescription:
	def __init__(self):
		self.reset()
	
	def reset(self):
		self.cdl = ""
		self.agentType = ""
		self.scaleVar = ""
		self.bindPoseData = None
		self.jointData = []
		self.geoDB = GeoDB()
		self.materialData = []
		self.actions = {}
		self.variables = {}
		# map Massive joint name to the joint object
		self.joints = {}
   
