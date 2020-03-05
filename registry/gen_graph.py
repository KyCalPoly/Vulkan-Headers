import re
import xml.etree.ElementTree as ElementTree
from argparse import ArgumentParser
from graphviz import Digraph
import graphviz

VK_XML_PATH = "./vk.xml"
EXTENSION_SUFFIXES = ['EXT', 'NV', 'NVX', 'INTEL']

argParser = ArgumentParser()

def main(args):
    vkDefs = gatherDefs()
    handleDependencies = traceHandleDependencies(vkDefs)
    dot = Digraph("Recursive Handle Dependencies", engine='dot')

    for handle, deps in handleDependencies.items():
        dot.node(handle,handle)
        for d in deps:
            dot.edge(handle, d[0], style='dashed' if d[1] else 'solid')
    
    dot.render('VkHandleCreation.gv', view=False, format='svg')

class CxxTypedVar:
    def __init__(self, typename, typrefix = '', tysuffix = '', **traits):
        self.typename = typename
        self.typrefix = typrefix
        self.tysuffix = tysuffix
        self.const = 'const' in typrefix
        self.ptr = '*' in tysuffix
        self.optional = traits['optional'] if 'optional' in traits else False
        self.traits = traits

    def isConst(self):
        return(self.const)
    def isPtr(self):
        return(self.ptr)
    def isConstPtr(self):
        return(self.isConst() and self.isPtr())
    def isNonConstPtr(self):
        return(self.isPtr() and not self.isConst())
    def isOptional(self):
        return(self.optional)
    def __str__(self):
        return(f"{self.typrefix} {self.typename}{self.tysuffix}".strip())
    def __repr__(self):
        return(f'[{str(self.traits)}] {str(self)};')

class TypeParam(CxxTypedVar):
    def __init__(self, typename, name, typrefix = '', tysuffix = '', namesuffix = '', **traits):
        CxxTypedVar.__init__(self, typename, typrefix=typrefix, tysuffix=tysuffix, **traits)
        self.name = name
        self.namesuffix = namesuffix

    @staticmethod
    def parseParam(paramElem):
        typrefix = ''
        tysuffix = ''
        namesuffix = ''
        typeEl = paramElem.find('type')
        typeStr = typeEl.text.strip()
        if paramElem.text != None:
            typrefix = paramElem.text.strip()
        if typeEl.tail != None:
            tysuffix = typeEl.tail.strip()
        nameEl = paramElem.find('name')
        name = nameEl.text.strip()
        namesuffix = nameEl.tail.strip() if nameEl.tail != None else ''
        optional = 'optional' in paramElem.attrib and paramElem.attrib['optional'] == 'true'
        return(TypeParam(typeStr, name, typrefix, tysuffix, namesuffix, optional=optional))
    
    def __str__(self):
        return(f"{self.typrefix} {self.typename}{self.tysuffix} {self.name}{self.namesuffix}".strip())

class Func:
    def __init__(self, fname:str, params:list):
        self.name = fname
        self.params = params
    
    def __str__(self):
        paramStrs = [str(p) for p in self.params]
        fproto = f"{self.name}({', '.join(paramStrs)});"
        if(len(fproto) > 120):
            pstring = ',\n    '.join(paramStrs)
            fproto = f"{self.name}(\n    {pstring}\n);"
        return(fproto)

class StructMember(CxxTypedVar):
    def __init__(self, typename, name, typexpr = None, **traits):
        CxxTypedVar.__init__(self, typename, **traits)
        self.typexpr = typexpr if typexpr != None else typename
        self.name = name

    @staticmethod
    def parseMember(el):
        tyPrefix = el.text if el.text != None else ''
        typeEl = el.find('type')
        tySuffix = typeEl.tail if typeEl.tail != None else ''
        nameEl = el.find('name')
        
        typeName = typeEl.text
        typeExpr = (tyPrefix + typeName + tySuffix).strip()
        name = nameEl.text
        optional = 'optional' in el.attrib and el.attrib['optional'] == 'true'
        return(StructMember(typeName, name, typeExpr, optional=optional))

    def __str__(self):
        return(f"{self.typexpr} {self.name}")

class StructType:
    def __init__(self, name, members = []):
        self.name = name
        self.members = members

    def __str__(self):
        memberString = ',\n    '.join([str(m) for m in self.members])
        return(f"struct {self.name}{{\n    {memberString}\n}}")

def gatherDefs():
    parser = ElementTree.iterparse(VK_XML_PATH)

    def isHandle(el):
        isHandleCategory = el.tag == "type" and 'category' in el.attrib and el.attrib['category'] == "handle"
        hasName = el.find('name') != None
        return(isHandleCategory and hasName)
    def isCommand(el):
        return(el.tag == 'command' and el.find('proto') != None)
    def isCreateInfo(el):
        isStruct = el.tag == 'type' and 'category' in el.attrib and el.attrib['category'] == 'struct'
        isCreateInfo = 'name' in el.attrib and el.attrib['name'].find('CreateInfo') >= 0 
        return(isStruct and isCreateInfo)

    handles = {}
    createCommands = {}
    createInfos = {}
    for _, el in parser:
        if(isHandle(el)):
            name = el.find('name').text
            if re.search(f"({'|'.join(EXTENSION_SUFFIXES)})$", name): continue # Skip extension defined handles
            parents = [s.strip() for s in el.attrib['parent'].split(',')] if 'parent' in el.attrib else []
            handles[name] = parents
        elif(isCommand(el)):
            fname = el.find('proto').find('name').text
            params = [TypeParam.parseParam(p) for p in el.findall('param')]
            if(fname[:8] == 'vkCreate' or fname[:10] == 'vkAllocate' or fname == 'vkGetDeviceQueue'):
                createCommands[fname] = Func(fname, params)
        elif(isCreateInfo(el)):
            structName = el.attrib['name'].strip()
            structMembers = [StructMember.parseMember(e) for e in el.findall('member')]
            createInfos[structName] = StructType(structName, structMembers)

    return({
        'handles': handles,
        'commands': createCommands,
        'createInfos': createInfos
    })

def expandInputs(initialInputs:list, vkDefs:dict, recursionLevel=0, parentIsOptional=False):
    inputs = []
    deepestR = 0
    for i in initialInputs:
        if(i.typename in vkDefs['createInfos']):
            stype = vkDefs['createInfos'][i.typename]
            subinputs, deepestSubR = expandInputs(stype.members, vkDefs, recursionLevel+1, i.isOptional() or parentIsOptional)
            inputs += subinputs
            deepestR = max(deepestR, deepestSubR)
        elif(i.typename in vkDefs['handles'] and (recursionLevel > 0 or not i.isNonConstPtr())): # Ignore pointer qualifications if this is a member of some createInfo struct we've recursed into. 
            inputs.append((i.typename, i.isOptional() or parentIsOptional))
    deepestR = max(deepestR, recursionLevel)
    return(inputs, deepestR)

def traceHandleDependencies(vkDefs):
    handleDependencies = {}
    for h, deps in vkDefs['handles'].items():
        handleDependencies[h] = set([(d, False) for d in deps])

    deepestOfAll = 0
    for _, func in vkDefs['commands'].items():
        inputs, deepestR = expandInputs(func.params, vkDefs)
        # print(f"Deepest recursive handle dependency for function '{func.name}' was {deepestR} recursions deep")
        deepestOfAll = max(deepestOfAll, deepestR)
        outputs = filter(lambda x: x.isNonConstPtr(), func.params)
        for o in outputs:
            if o.typename in handleDependencies:
                for i in inputs:
                    handleDependencies[o.typename].add(i)
    # print(f"Deepest recursion of all was {deepestOfAll} recursions deep")
    return(handleDependencies)
            

if __name__ == '__main__':
    main(argParser.parse_args())